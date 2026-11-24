"""Host autorizador simulado (el banco).

Mantiene cuentas, saldos, créditos y remesas, y responde mensajes ISO 8583.
Es un sistema de mensaje único: la 0200 aprobada ya afecta el saldo; la 0420
lo corrige. Dos propiedades sostienen el "exactamente una vez" del lado del
banco:

* una 0200 repetida (misma terminal, STAN y fecha de transmisión) regresa la
  respuesta original sin volver a cargar;
* una reversa repetida (0420 o 0421 con los mismos datos originales) se acusa
  de nuevo sin volver a abonar.

Las tarjetas se registran por token: el host tampoco guarda el PAN en claro.

El límite de retiro es por DÍA DE NEGOCIO del banco, en hora de la Ciudad de México (sin horario de verano
desde 2022): el acumulado se reinicia a medianoche local, no a medianoche UTC. Una reversa que llega otro
día no libera el límite del día nuevo.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from till import seguridad
from till.host import iso
from till.host.iso import MTI, Respuesta

Llave = tuple[str, str, str]  # (terminal, STAN, fecha de transmisión)


@dataclass
class CuentaHost:
    id: str
    saldo: int
    limite_retiro_diario: int
    retirado_hoy: int = 0
    dia: date | None = None  # día de negocio al que corresponde ``retirado_hoy``


@dataclass
class OperacionHost:
    llave: Llave
    tipo: str
    monto: int
    cuenta: str | None
    destino: str | None
    respuesta: str
    autorizacion: str | None
    terminal: str
    revertido: int = 0
    reversa_aplicada: bool = False
    avisos_reversa: int = 0
    campos_respuesta: dict[str, str] = field(default_factory=dict)
    dia: date | None = None  # día de negocio en que se aplicó (sólo retiros aprobados)


@dataclass
class Remesa:
    clave: str
    monto: int
    beneficiario: str
    vigente: bool = True


class HostAutorizador:
    def __init__(
        self,
        tokenizador: seguridad.Tokenizador | None = None,
        validar_criptograma: bool = True,
        latencia: float = 0.0,
        reloj: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.tokenizador = tokenizador or seguridad.Tokenizador()
        self.validar_criptograma = validar_criptograma
        self.latencia = latencia
        self.reloj = reloj
        self.cuentas: dict[str, CuentaHost] = {}
        self.tarjetas: dict[str, str] = {}  # token -> cuenta
        self.creditos: dict[str, int] = {}  # crédito -> adeudo en centavos
        self.remesas: dict[str, Remesa] = {}
        self.operaciones: dict[Llave, OperacionHost] = {}
        self.reversas_sin_original: set[Llave] = set()
        self.mensajes_recibidos = 0
        self._autorizaciones = itertools.count(100001)

    # -- alta de datos de prueba ------------------------------------------------
    def alta_cuenta(self, id_: str, saldo: int, limite_retiro_diario: int = 5_000_000) -> CuentaHost:
        c = CuentaHost(id_, saldo, limite_retiro_diario)
        self.cuentas[id_] = c
        return c

    def alta_tarjeta(self, pan: str, cuenta: str) -> str:
        token = self.tokenizador.token(pan)
        self.tarjetas[token] = cuenta
        return token

    def alta_credito(self, id_: str, adeudo: int) -> None:
        self.creditos[id_] = adeudo

    def saldo(self, cuenta: str) -> int:
        return self.cuentas[cuenta].saldo

    def dia_de_negocio(self) -> date:
        return self.reloj().astimezone(iso.ZONA_LOCAL).date()

    # -- entrada de mensajes ----------------------------------------------------
    async def procesar(self, raw: bytes) -> bytes:
        if self.latencia:
            await asyncio.sleep(self.latencia)
        # Todo lo que sigue es síncrono: cada mensaje se aplica de forma atómica.
        self.mensajes_recibidos += 1
        msg = iso.decodificar(raw)
        mti = msg["t"]
        if mti == MTI.SOLICITUD:
            resp = self._financiera(msg)
        elif mti in (MTI.REVERSA, MTI.REVERSA_REPETICION):
            resp = self._reversa(msg)
        elif mti == MTI.RED:
            resp = {"t": MTI.RESPUESTA_RED, "7": msg["7"], "11": msg["11"], "39": Respuesta.APROBADA, "70": msg.get("70", "301")}
        else:
            resp = {"t": f"{int(mti) + 10:04d}", "11": msg.get("11", "000000"), "39": Respuesta.TRANSACCION_INVALIDA}
        return iso.codificar(resp)

    # -- 0200 -------------------------------------------------------------------
    def _financiera(self, m: dict[str, str]) -> dict[str, str]:
        llave: Llave = (m["41"], m["11"], m["7"])
        previa = self.operaciones.get(llave)
        if previa is not None:  # retransmisión: misma respuesta, sin doble efecto
            return dict(previa.campos_respuesta)

        tipo = iso.TIPO_POR_PROCESO.get(m["3"][:2])
        monto = int(m["4"])
        token = self.tokenizador.token(m["2"]) if "2" in m else None
        cuenta = self.tarjetas.get(token) if token else None
        destino = m.get("103")
        codigo = Respuesta.APROBADA
        saldo_final: int | None = None
        dia: date | None = None

        if llave in self.reversas_sin_original:
            codigo = Respuesta.TRANSACCION_INVALIDA  # ya llegó su reversa: no se aplica
        elif tipo is None or m.get("49") != iso.MONEDA_MXN or monto <= 0:
            codigo = Respuesta.TRANSACCION_INVALIDA
        elif cuenta is None or cuenta not in self.cuentas:
            codigo = Respuesta.TARJETA_INVALIDA
        elif self.validar_criptograma and not self._criptograma_valido(m, monto):
            codigo = Respuesta.NO_HONRAR
        else:
            c = self.cuentas[cuenta]
            if tipo == "retiro":
                hoy = self.dia_de_negocio()
                if c.dia != hoy:  # primer retiro del día de negocio: el acumulado empieza en cero
                    c.dia, c.retirado_hoy = hoy, 0
                if c.saldo < monto:
                    codigo = Respuesta.FONDOS_INSUFICIENTES
                elif c.retirado_hoy + monto > c.limite_retiro_diario:
                    codigo = Respuesta.EXCEDE_LIMITE
                else:
                    c.saldo -= monto
                    c.retirado_hoy += monto
                    saldo_final = c.saldo
                    dia = hoy
            elif tipo == "deposito":
                c.saldo += monto
                saldo_final = c.saldo
            elif tipo == "pago_credito":
                if destino not in self.creditos:
                    codigo = Respuesta.TRANSACCION_INVALIDA
                elif monto > self.creditos[destino]:
                    codigo = Respuesta.MONTO_INVALIDO
                else:
                    self.creditos[destino] -= monto
            elif tipo == "remesa":
                if not destino:
                    codigo = Respuesta.TRANSACCION_INVALIDA

        autorizacion = f"{next(self._autorizaciones) % 1_000_000:06d}" if codigo == Respuesta.APROBADA else None
        if tipo == "remesa" and autorizacion:
            self.remesas[autorizacion] = Remesa(autorizacion, monto, destino or "")

        resp = {k: m[k] for k in ("3", "4", "7", "11", "12", "13", "37", "41", "42", "49") if k in m}
        resp["t"] = MTI.RESPUESTA
        resp["39"] = codigo
        if autorizacion:
            resp["38"] = autorizacion
        if saldo_final is not None:
            resp["54"] = iso.campo_54(saldo_final)
        self.operaciones[llave] = OperacionHost(
            llave, tipo or "?", monto, cuenta, destino, codigo, autorizacion, m["41"], campos_respuesta=resp, dia=dia
        )
        return dict(resp)

    def _criptograma_valido(self, m: dict[str, str], monto: int) -> bool:
        if "55" not in m or "2" not in m:
            return False
        try:
            recibido, atc = seguridad.leer_tlv_icc(m["55"])
        except ValueError:
            return False
        esperado = seguridad.arqc(seguridad.llave_tarjeta(m["2"]), monto, m["11"], m["41"], atc)
        return recibido == esperado

    # -- 0420 / 0421 --------------------------------------------------------------
    def _reversa(self, m: dict[str, str]) -> dict[str, str]:
        orig = iso.leer_campo_90(m["90"])
        llave: Llave = (m["41"], orig["stan"], orig["f7"])
        reemplazo = iso.leer_campo_95(m.get("95"))
        op = self.operaciones.get(llave)
        if op is None:
            # La 0200 nunca llegó (o llegará tarde): se acusa y se bloquea.
            self.reversas_sin_original.add(llave)
        else:
            op.avisos_reversa += 1
            if op.respuesta == Respuesta.APROBADA and not op.reversa_aplicada:
                self._deshacer(op, max(0, min(op.monto, op.monto - reemplazo)))
        resp = {k: m[k] for k in ("3", "4", "7", "11", "37", "41", "42", "49", "90", "95") if k in m}
        resp["t"] = MTI.ACUSE_REVERSA
        resp["39"] = Respuesta.APROBADA
        return resp

    def _deshacer(self, op: OperacionHost, monto: int) -> None:
        op.reversa_aplicada = True
        op.revertido = monto
        if monto == 0:
            return
        c = self.cuentas.get(op.cuenta or "")
        if op.tipo == "retiro" and c:
            c.saldo += monto
            if op.dia == c.dia:  # la reversa de un retiro de otro día no libera el límite de hoy
                c.retirado_hoy -= monto
        elif op.tipo == "deposito" and c:
            c.saldo -= monto
        elif op.tipo == "pago_credito" and op.destino in self.creditos:
            self.creditos[op.destino] += monto
        elif op.tipo == "remesa" and op.autorizacion in self.remesas:
            r = self.remesas[op.autorizacion]
            r.monto -= monto
            r.vigente = r.monto > 0

    # -- reportes -------------------------------------------------------------------
    def totales_terminal(self, terminal: str) -> dict[str, int]:
        """Totales netos aprobados por tipo (lo que el banco liquidaría)."""
        tot = {t: 0 for t in iso.PROCESO}
        for op in self.operaciones.values():
            if op.terminal == terminal and op.respuesta == Respuesta.APROBADA:
                tot[op.tipo] = tot.get(op.tipo, 0) + op.monto - op.revertido
        return tot

    def resumen(self) -> dict[str, Any]:
        return {
            "cuentas": {k: v.saldo for k, v in self.cuentas.items()},
            "creditos": dict(self.creditos),
            "remesas_vigentes": sum(1 for r in self.remesas.values() if r.vigente),
            "operaciones": len(self.operaciones),
            "mensajes_recibidos": self.mensajes_recibidos,
        }
