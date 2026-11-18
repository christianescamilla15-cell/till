"""Reversas con almacenar y reenviar.

Una reversa encolada no se pierde: vive en la bitácora hasta que llega su 0430.
El primer envío es 0420 y los reintentos son 0421 (repetición). El host es
idempotente con las reversas, así que reenviar de más es seguro; lo que nunca
puede pasar es dejar de reenviar. Espera entre intentos: exponencial con tope.

Al llegar el acuse, en una sola transacción: la reversa queda ACUSADA, la
operación pasa a REVERTIDA (o PARCIAL) y, si hubo entrega parcial, el efectivo
en tránsito se reconoce en la cuenta de compensación con el banco.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from till.core.bitacora import Bitacora, Estado
from till.db import BaseDatos
from till.host.cliente import ClienteHost, DatosTerminal, ahora_utc
from till.host.enlace import HostNoDisponible, TiempoAgotadoHost
from till.ledger import LibroMayor, cuentas

log = logging.getLogger("till.reversas")


class ReenvioReversas:
    def __init__(
        self,
        db: BaseDatos,
        bitacora: Bitacora,
        libro: LibroMayor,
        cliente: ClienteHost,
        reloj: Callable[[], datetime] = ahora_utc,
        espera_base: float = 0.5,
        espera_max: float = 300.0,
    ):
        self.db = db
        self.bitacora = bitacora
        self.libro = libro
        self.cliente = cliente
        self.reloj = reloj
        self.espera_base = espera_base
        self.espera_max = espera_max
        self.terminales: dict[str, DatosTerminal] = {}
        self._en_vuelo: set[int] = set()
        self.enviadas = 0
        self.acusadas = 0

    def registrar_terminal(self, dt: DatosTerminal) -> None:
        self.terminales[dt.terminal] = dt

    async def intentar(self, rev: dict[str, Any]) -> bool:
        if rev["id"] in self._en_vuelo:
            return False
        # Se relee: otro intento (el inmediato del cajero o el ciclo de fondo) pudo acusarla ya.
        rev = self.bitacora.reversa_de(rev["tx_id"]) or rev
        if rev["estado"] != "PENDIENTE":
            return False
        dt = self.terminales[rev["terminal"]]
        self._en_vuelo.add(rev["id"])
        try:
            momento = self.reloj()
            stan = self.bitacora.siguiente_stan(dt.terminal)
            msg = self.cliente.reversa(dt, rev, stan, momento, repeticion=rev["intentos"] > 0)
            self.enviadas += 1
            try:
                ok = await self.cliente.revertir(msg)
                error = "" if ok else "acuse sin código 00"
            except (TiempoAgotadoHost, HostNoDisponible) as e:
                ok, error = False, f"{type(e).__name__}: {e}"
            if ok:
                self._acusar(rev)
                return True
            espera = min(self.espera_max, self.espera_base * (2 ** rev["intentos"]))
            self.bitacora.registrar_intento_reversa(rev["id"], error, momento.timestamp() + espera)
            log.warning("reversa %s sin acuse (intento %s): %s", rev["tx_id"], rev["intentos"] + 1, error)
            return False
        finally:
            self._en_vuelo.discard(rev["id"])

    def _acusar(self, rev: dict[str, Any]) -> None:
        with self.db.transaccion():
            if not self.bitacora.acusar_reversa(rev["id"]):
                return
            tx_id, terminal, reemplazo = rev["tx_id"], rev["terminal"], int(rev["reemplazo"])
            if reemplazo > 0:
                self.libro.registrar(
                    f"{tx_id}:transito:acuse", "retiro", cuentas.confirmar_transito(terminal, reemplazo), tx_id
                )
                self.bitacora.transicion(tx_id, Estado.PARCIAL, detalle="0430 recibido (parcial)")
            else:
                self.bitacora.transicion(tx_id, Estado.REVERTIDA, detalle="0430 recibido")
        self.acusadas += 1

    async def intentar_tx(self, tx_id: str) -> bool | None:
        rev = self.bitacora.reversa_de(tx_id)
        if rev is None:
            return None
        return await self.intentar(rev)

    async def drenar(self, terminal: str | None = None, respetar_espera: bool = False) -> dict[str, int]:
        """Un intento por cada reversa pendiente."""
        hasta = self.reloj().timestamp() if respetar_espera else None
        pendientes = self.bitacora.reversas_pendientes(terminal, hasta)
        acusadas = 0
        for rev in pendientes:
            if await self.intentar(rev):
                acusadas += 1
        return {
            "intentadas": len(pendientes),
            "acusadas": acusadas,
            "pendientes": len(self.bitacora.reversas_pendientes(terminal)),
        }

    async def correr(self, detener: asyncio.Event, intervalo: float = 1.0) -> None:
        """Ciclo de fondo: reintenta lo pendiente respetando la espera exponencial."""
        while not detener.is_set():
            try:
                await self.drenar(respetar_espera=True)
            except Exception:  # el ciclo de reenvío nunca debe morir por un error puntual
                log.exception("error en el ciclo de reversas")
            try:
                await asyncio.wait_for(detener.wait(), intervalo)
            except asyncio.TimeoutError:
                pass
