"""Máquina de estados por transacción: AUTORIZAR -> ENTREGAR/RECIBIR -> CONFIRMAR.

Principio rector: una operación no cuenta hasta que el dispositivo confirma y
el banco acusa recibo.

* Retiro: se denomina (¿hay billetes?), se autoriza (0200/0210), se entrega y
  sólo cuando el dispositivo confirma que el cliente tomó el efectivo se
  registra el asiento. Si algo falla después de autorizar se encola una
  reversa (0420) por lo que no se entregó: total, o parcial con el campo 95.
* Depósito, pago de crédito y remesa: los billetes se cuentan en escrow, se
  autoriza, y sólo cuando el dispositivo confirma que los guardó se registra
  el asiento. Si algo falla después de autorizar: reversa total y se intenta
  devolver el efectivo; si el dispositivo se lo queda, el monto queda en
  aclaraciones a nombre de la operación.

Exactamente una vez: llave de idempotencia por solicitud (bitácora), llaves
por asiento (libro mayor), y un host que no aplica dos veces la misma 0200 ni
la misma reversa.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from till import dinero
from till.core.bitacora import Bitacora, Estado
from till.core.reversas import ReenvioReversas
from till.db import BaseDatos
from till.devices import (
    Camara,
    CorteDeEnergia,
    ImpresoraComprobantes,
    LectorTarjetas,
    RecicladorEfectivo,
    SesionTarjeta,
)
from till.dinero import Mezcla
from till.host import iso
from till.host.cliente import ClienteHost, DatosTerminal, RespuestaHost, ahora_utc
from till.host.enlace import HostNoDisponible, TiempoAgotadoHost
from till.host.iso import MotivoReversa
from till.ledger import LibroMayor, cuentas

log = logging.getLogger("till.cajero")

TIPOS = ("retiro", "deposito", "pago_credito", "remesa")


class SolicitudInvalida(ValueError):
    pass


class CajeroFueraDeServicio(RuntimeError):
    pass


@dataclass
class Solicitud:
    tipo: str
    llave_idem: str
    monto: int = 0  # centavos; sólo retiro
    billetes: Mezcla = field(default_factory=dict)  # sólo ingresos
    destino: str | None = None  # crédito o beneficiario

    def validar(self) -> None:
        if self.tipo not in TIPOS:
            raise SolicitudInvalida(f"tipo desconocido: {self.tipo}")
        if not self.llave_idem or len(self.llave_idem) > 128:
            raise SolicitudInvalida("la llave de idempotencia es obligatoria (máx. 128)")
        if self.tipo == "retiro" and (self.monto <= 0 or self.monto % 100):
            raise SolicitudInvalida("el retiro necesita un monto en pesos enteros")
        if self.tipo != "retiro" and not any(n > 0 for n in self.billetes.values()):
            raise SolicitudInvalida("un ingreso de efectivo necesita billetes")
        if self.tipo in ("pago_credito", "remesa") and not self.destino:
            raise SolicitudInvalida("pago de crédito y remesa necesitan destino")
        if self.destino is not None and not destino_valido(self.destino):
            # Se valida aquí, antes de tocar dispositivos: si el campo 103 no se pudiera codificar,
            # la 0200 fallaría con la operación ya en AUTORIZANDO y el efectivo en escrow.
            raise SolicitudInvalida(
                f"el destino viaja en el campo 103 de ISO 8583: hasta {MAX_DESTINO} caracteres ASCII imprimibles"
            )


MAX_DESTINO = 28  # campo 103 (LLVAR, ASCII)


def destino_valido(destino: str) -> bool:
    return 0 < len(destino) <= MAX_DESTINO and destino.isascii() and destino.isprintable()


@dataclass
class Resultado:
    tx_id: str
    tipo: str
    estado: str
    monto: int
    entregado: int
    respuesta: str | None
    autorizacion: str | None
    enmascarado: str | None
    aclaracion: int
    motivo: str | None
    reversa_pendiente: bool
    repetida: bool = False

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


_SIN_RESPUESTA = object()


class Cajero:
    def __init__(
        self,
        datos: DatosTerminal,
        db: BaseDatos,
        bitacora: Bitacora,
        libro: LibroMayor,
        cliente: ClienteHost,
        reversas: ReenvioReversas,
        reciclador: RecicladorEfectivo,
        lector: LectorTarjetas,
        impresora: ImpresoraComprobantes,
        camara: Camara,
        reloj: Callable[[], datetime] = ahora_utc,
    ):
        self.datos = datos
        self.terminal = datos.terminal
        self.db = db
        self.bitacora = bitacora
        self.libro = libro
        self.cliente = cliente
        self.reversas = reversas
        self.reciclador = reciclador
        self.lector = lector
        self.impresora = impresora
        self.camara = camara
        self.reloj = reloj
        self.caido = False
        self._candado = asyncio.Lock()
        reversas.registrar_terminal(datos)

    @property
    def dispositivos(self) -> dict[str, Any]:
        return {"efectivo": self.reciclador, "tarjeta": self.lector, "impresora": self.impresora, "camara": self.camara}

    # =========================================================================
    async def operar(self, s: Solicitud, pan: str | None = None) -> Resultado:
        """Ejecuta una operación completa. ``pan`` simula insertar la tarjeta."""
        s.validar()
        if self.caido:
            raise CajeroFueraDeServicio(f"{self.terminal}: fuera de servicio, requiere reinicio")
        async with self._candado:
            previa = self.bitacora.por_llave(s.llave_idem)
            if previa is not None:
                return self._resultado(previa["id"], repetida=True)
            if pan is not None:
                self.lector.insertar(pan)
            tx = self.bitacora.crear(self.terminal, s.tipo, s.llave_idem, s.destino, s.monto)
            tx_id = tx["id"]
            try:
                await self._evidencia(tx_id)
                sesion = await self._leer_tarjeta(tx_id)
                if sesion is not None:
                    try:
                        if s.tipo == "retiro":
                            await self._retiro(tx_id, s, sesion)
                        else:
                            await self._ingreso(tx_id, s, sesion)
                    finally:
                        sesion.olvidar()  # el PAN sale de memoria pase lo que pase
                await self._expulsar_tarjeta()
                await self.reversas.intentar_tx(tx_id)  # primer intento inmediato
                await self._comprobante(tx_id)
            except CorteDeEnergia:
                # Un corte apaga todo el cajero, no sólo el dispositivo que lo reportó.
                self.caido = True
                for dev in self.dispositivos.values():
                    if dev.encendido:
                        dev._apagar()
                raise
            return self._resultado(tx_id)

    # -- pasos comunes -----------------------------------------------------------
    async def _evidencia(self, tx_id: str) -> None:
        t = await self.camara.ejecutar("Camera.TakePicture", referencia=tx_id)
        if t.ok:
            self.bitacora.actualizar(tx_id, evidencia=t.datos["sha256"])

    async def _leer_tarjeta(self, tx_id: str) -> SesionTarjeta | None:
        t = await self.lector.ejecutar("CardReader.ReadRawData")
        if not t.ok or self.lector.sesion is None:
            self.bitacora.transicion(tx_id, Estado.CANCELADA, motivo=f"lectura de tarjeta: {t.error}")
            return None
        self.bitacora.actualizar(tx_id, token=t.datos["token"], enmascarado=t.datos["enmascarado"])
        return self.lector.sesion

    async def _expulsar_tarjeta(self) -> None:
        if self.lector.con_tarjeta:
            await self.lector.ejecutar("CardReader.Eject")

    async def _autorizar(
        self, tx_id: str, tipo: str, monto: int, sesion: SesionTarjeta, destino: str | None
    ) -> RespuestaHost | object | None:
        """Regresa la respuesta del host, ``_SIN_RESPUESTA`` o ``None`` si no se pudo ni enviar."""
        stan = self.bitacora.siguiente_stan(self.terminal)
        momento = self.reloj()
        f7, rrn = iso.campo_7(momento), iso.rrn(momento, stan)
        ac = await self.lector.ejecutar("CardReader.GenerateAC", monto=monto, stan=stan, terminal=self.terminal)
        if not ac.ok:
            self.bitacora.transicion(tx_id, Estado.CANCELADA, motivo=f"chip: {ac.error}")
            return None
        # Write-ahead: STAN, fecha y RRN quedan en la bitácora ANTES de enviar la 0200.
        self.bitacora.transicion(tx_id, Estado.AUTORIZANDO, stan=stan, f7=f7, rrn=rrn, monto=monto)
        msg = self.cliente.financiera(
            self.datos, tipo, monto, stan, f7, rrn, momento, sesion.pan, ac.datos["campo55"], destino
        )
        try:
            resp = await self.cliente.solicitar(msg)
        except (TiempoAgotadoHost, HostNoDisponible) as e:
            log.warning("%s: 0200 sin respuesta (%s)", self.terminal, e)
            return _SIN_RESPUESTA
        finally:
            msg.clear()
        if not resp.aprobada:
            self.bitacora.transicion(
                tx_id, Estado.RECHAZADA, respuesta=resp.codigo, motivo=f"banco: {resp.descripcion}"
            )
        else:
            self.bitacora.transicion(
                tx_id, Estado.AUTORIZADA, respuesta=resp.codigo, autorizacion=resp.autorizacion, saldo=resp.saldo
            )
        return resp

    # -- retiro ---------------------------------------------------------------------
    async def _retiro(self, tx_id: str, s: Solicitud, sesion: SesionTarjeta) -> None:
        d = await self.reciclador.ejecutar("CashDispenser.Denominate", monto=s.monto)
        if not d.ok:
            self.bitacora.transicion(tx_id, Estado.CANCELADA, motivo="monto no disponible en este cajero")
            return
        mezcla: Mezcla = d.datos["mezcla"]
        self.bitacora.actualizar(tx_id, mezcla=mezcla)

        r = await self._autorizar(tx_id, "retiro", s.monto, sesion, None)
        if r is _SIN_RESPUESTA:
            self.bitacora.encolar_reversa(tx_id, 0, MotivoReversa.SIN_RESPUESTA, motivo="sin respuesta del banco")
            return
        if not isinstance(r, RespuestaHost) or not r.aprobada:
            return

        self.bitacora.transicion(tx_id, Estado.ENTREGANDO)
        t = await self.reciclador.ejecutar("CashDispenser.Dispense", referencia=tx_id, mezcla=mezcla)
        entregable: Mezcla = t.datos.get("entregable", {})
        rechazo: Mezcla = t.datos.get("rechazo", {})
        retirado: Mezcla = {}
        retenido: Mezcla = {}
        if entregable:
            p = await self.reciclador.ejecutar("CashDispenser.Present", referencia=tx_id)
            retirado = p.datos.get("retirado", {})
            retenido = p.datos.get("retenido", {})
        falla = None if t.ok else t.error
        self._cerrar_retiro(tx_id, mezcla, retirado, rechazo, retenido, falla)

    def _cerrar_retiro(
        self,
        tx_id: str,
        solicitado: Mezcla,
        retirado: Mezcla,
        rechazo: Mezcla,
        retenido: Mezcla,
        falla: str | None = None,
    ) -> None:
        """Registra lo que físicamente pasó y decide: confirmar o revertir (total o parcial)."""
        t = self.terminal
        monto = dinero.valor(solicitado)
        entregado = dinero.valor(retirado)
        with self.db.transaccion():
            if rechazo:
                self.libro.registrar(f"{tx_id}:rechazo", "interno", cuentas.mover(t, rechazo, "casete", "rechazo"), tx_id)
            if retenido:
                self.libro.registrar(f"{tx_id}:retenido", "interno", cuentas.mover(t, retenido, "casete", "retenido"), tx_id)
            if entregado == monto:
                self.libro.registrar(f"{tx_id}:retiro", "retiro", cuentas.retiro(t, retirado), tx_id)
                self.bitacora.transicion(tx_id, Estado.CONFIRMADA, detalle="efectivo tomado", entregado=entregado)
            elif entregado > 0:
                self.libro.registrar(f"{tx_id}:transito", "retiro", cuentas.retiro_en_transito(t, retirado), tx_id)
                self.bitacora.encolar_reversa(
                    tx_id, entregado, MotivoReversa.PARCIAL, entregado=entregado,
                    motivo=f"entrega parcial ({falla or 'dispositivo'})",
                )
            else:
                motivo = "efectivo no retirado" if retenido and not rechazo else f"falla de entrega ({falla or 'dispositivo'})"
                self.bitacora.encolar_reversa(tx_id, 0, MotivoReversa.FALLA_DISPOSITIVO, motivo=motivo)

    # -- ingresos: depósito, pago de crédito, remesa ------------------------------
    async def _ingreso(self, tx_id: str, s: Solicitud, sesion: SesionTarjeta) -> None:
        self.bitacora.transicion(tx_id, Estado.CONTANDO)
        await self.reciclador.ejecutar("CashAcceptor.CashInStart", referencia=tx_id)
        c = await self.reciclador.ejecutar("CashAcceptor.CashIn", referencia=tx_id, billetes=s.billetes)
        contado: Mezcla = c.datos.get("aceptados", {}) if c.ok else {}
        if not contado:
            await self._devolver_escrow(tx_id)
            motivo = f"conteo: {c.error}" if not c.ok else "ningún billete aceptado"
            self.bitacora.transicion(tx_id, Estado.CANCELADA, motivo=motivo)
            return
        monto = dinero.valor(contado)
        self.bitacora.actualizar(tx_id, mezcla=contado, monto=monto)

        r = await self._autorizar(tx_id, s.tipo, monto, sesion, s.destino)
        if r is _SIN_RESPUESTA:
            self.bitacora.encolar_reversa(tx_id, 0, MotivoReversa.SIN_RESPUESTA, motivo="sin respuesta del banco")
            await self._devolver_escrow(tx_id)
            return
        if not isinstance(r, RespuestaHost) or not r.aprobada:
            await self._devolver_escrow(tx_id)
            return

        self.bitacora.transicion(tx_id, Estado.RECIBIENDO)
        e = await self.reciclador.ejecutar("CashAcceptor.CashInEnd", referencia=tx_id)
        if e.ok:
            self._confirmar_ingreso(tx_id, s.tipo, monto, e.datos["destinos"])
            return
        self.bitacora.encolar_reversa(
            tx_id, 0, MotivoReversa.FALLA_DISPOSITIVO, motivo=f"no se pudo guardar el efectivo ({e.error})"
        )
        await self._devolver_escrow(tx_id)

    def _confirmar_ingreso(self, tx_id: str, tipo: str, monto: int, destinos: dict[str, Mezcla]) -> None:
        destinos = {k: {int(d): n for d, n in v.items()} for k, v in destinos.items()}
        with self.db.transaccion():
            self.libro.registrar(f"{tx_id}:ingreso", tipo, cuentas.ingreso(self.terminal, tipo, destinos), tx_id)
            self.bitacora.transicion(tx_id, Estado.CONFIRMADA, detalle="efectivo guardado", entregado=monto)

    async def _devolver_escrow(self, tx_id: str) -> None:
        r = await self.reciclador.ejecutar("CashAcceptor.CashInRollback", referencia=tx_id)
        if not r.ok and r.datos.get("retenido"):
            self._registrar_retenido(tx_id, r.datos["retenido"])

    def _registrar_retenido(self, tx_id: str, retenido: Mezcla) -> None:
        retenido = {int(d): n for d, n in retenido.items()}
        with self.db.transaccion():
            self.libro.registrar(
                f"{tx_id}:aclaracion", "aclaracion", cuentas.retenido_por_aclarar(self.terminal, retenido), tx_id
            )
            self.bitacora.actualizar(tx_id, aclaracion=dinero.valor(retenido))

    # -- comprobante ------------------------------------------------------------------
    async def _comprobante(self, tx_id: str) -> None:
        tx = self.bitacora.obtener(tx_id)
        if tx is None or not tx["enmascarado"]:
            return
        lineas = [
            "TILL - COMPROBANTE (SIMULADO)",
            f"TERMINAL {self.terminal}",
            f"FECHA {tx['actualizado'][:19]}Z",
            f"OPERACION {tx['tipo'].upper()}  ESTADO {tx['estado']}",
            f"TARJETA {tx['enmascarado']}",
            f"MONTO {dinero.formatear(tx['monto'])}",
        ]
        if tx["autorizacion"]:
            lineas.append(f"AUTORIZACION {tx['autorizacion']}")
        if tx["tipo"] == "remesa" and tx["estado"] == Estado.CONFIRMADA:
            lineas.append(f"CLAVE DE COBRO {tx['autorizacion']}")
        if tx["aclaracion"]:
            lineas.append(f"EFECTIVO RETENIDO {dinero.formatear(tx['aclaracion'])} - ACLARACION {tx_id[:8]}")
        t = await self.impresora.ejecutar("Printer.PrintForm", lineas=lineas, referencia=tx_id)
        # Una falla de impresora no revierte nada: sólo se anota.
        self.bitacora.actualizar(tx_id, comprobante="impreso" if t.ok else f"no impreso: {t.error}")

    # -- resultado ----------------------------------------------------------------------
    def _resultado(self, tx_id: str, repetida: bool = False) -> Resultado:
        tx = self.bitacora.obtener(tx_id)
        assert tx is not None
        rev = self.bitacora.reversa_de(tx_id)
        return Resultado(
            tx_id=tx_id,
            tipo=tx["tipo"],
            estado=tx["estado"],
            monto=tx["monto"],
            entregado=tx["entregado"],
            respuesta=tx["respuesta"],
            autorizacion=tx["autorizacion"],
            enmascarado=tx["enmascarado"],
            aclaracion=tx["aclaracion"],
            motivo=tx["motivo"],
            reversa_pendiente=bool(rev and rev["estado"] == "PENDIENTE"),
            repetida=repetida,
        )

    def resultado(self, tx_id: str) -> Resultado:
        return self._resultado(tx_id)

    # =========================================================================
    async def recuperar(self) -> dict[str, Any]:
        """Arranque tras una caída: retoma cada transacción que no llegó a un estado final.

        La fuente de verdad de lo físico es el dispositivo (su memoria no
        volátil); la de lo contable, la bitácora. Lo que no se pueda probar que
        se entregó, se revierte.
        """
        async with self._candado:
            arranque = {}
            for nombre, dev in self.dispositivos.items():
                if not dev.encendido:
                    arranque[nombre] = await dev.encender()
            self.caido = False
            acciones: list[dict[str, Any]] = []
            for tx in self.bitacora.pendientes(self.terminal):
                accion = await self._retomar(tx)
                acciones.append({"tx_id": tx["id"], "estado_previo": tx["estado"], "accion": accion})
            drenado = await self.reversas.drenar(self.terminal)
            return {"arranque_dispositivos": arranque, "acciones": acciones, "reversas": drenado}

    async def _retomar(self, tx: dict[str, Any]) -> str:
        tx_id, estado, tipo = tx["id"], Estado(tx["estado"]), tx["tipo"]
        if estado is Estado.EN_REVERSA:
            return "reversa ya encolada"
        if estado is Estado.INICIADA:
            self.bitacora.transicion(tx_id, Estado.CANCELADA, motivo="interrumpida antes de autorizar")
            return "cancelada"
        if estado is Estado.CONTANDO:
            await self._retener_si_aplica(tx_id)
            self.bitacora.transicion(tx_id, Estado.CANCELADA, motivo="interrumpida durante el conteo")
            return "cancelada"
        if estado is Estado.AUTORIZANDO:
            # No sabemos si la 0200 llegó: se revierte (el host tolera reversas sin original).
            self.bitacora.encolar_reversa(tx_id, 0, MotivoReversa.SIN_RESPUESTA, motivo="caída esperando la 0210")
            if tipo != "retiro":
                await self._retener_si_aplica(tx_id)
            return "reversa total"
        if tipo == "retiro":
            st = (await self.reciclador.ejecutar("CashDispenser.GetPresentStatus", referencia=tx_id)).datos
            if st.get("fase") == "sin_operacion":
                self.bitacora.encolar_reversa(tx_id, 0, MotivoReversa.FALLA_DISPOSITIVO, motivo="caída antes de entregar")
                return "reversa total"
            if estado is Estado.AUTORIZADA:
                self.bitacora.transicion(tx_id, Estado.ENTREGANDO)
            self._cerrar_retiro(
                tx_id,
                tx["mezcla"],
                _m(st.get("entregado")),
                _m(st.get("rechazo")),
                _m(st.get("retenido")),
                "corte de energía",
            )
            nuevo = self.bitacora.obtener(tx_id)
            return "confirmada" if nuevo and nuevo["estado"] == Estado.CONFIRMADA else "reversa"
        # ingresos ya autorizados
        st = (await self.reciclador.ejecutar("CashAcceptor.GetCashInStatus", referencia=tx_id)).datos
        if estado is Estado.AUTORIZADA:
            self.bitacora.transicion(tx_id, Estado.RECIBIENDO)
        if st.get("fase") == "almacenado":
            self._confirmar_ingreso(tx_id, tipo, tx["monto"], st.get("destinos", {}))
            return "confirmada"
        self.bitacora.encolar_reversa(tx_id, 0, MotivoReversa.FALLA_DISPOSITIVO, motivo="caída antes de guardar el efectivo")
        if st.get("fase") == "retenido":
            self._registrar_retenido(tx_id, _m(st.get("retenido")))
        elif st.get("fase") == "en_escrow":
            await self._devolver_escrow(tx_id)
        return "reversa total"

    async def _retener_si_aplica(self, tx_id: str) -> None:
        st = (await self.reciclador.ejecutar("CashAcceptor.GetCashInStatus", referencia=tx_id)).datos
        if st.get("fase") == "retenido" and st.get("retenido"):
            self._registrar_retenido(tx_id, _m(st["retenido"]))
        elif st.get("fase") == "en_escrow":
            await self._devolver_escrow(tx_id)


def _m(x: Any) -> Mezcla:
    return {int(d): int(n) for d, n in (x or {}).items() if n}
