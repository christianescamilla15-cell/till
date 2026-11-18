"""Cliente del host: arma los mensajes ISO 8583 del lado del cajero.

No guarda estado: el STAN lo asigna la bitácora (persistente) y los datos de la
reversa salen de la bitácora, que nunca tiene el PAN. Por eso la 0420 identifica
la operación original con el campo 90 (MTI, STAN y fecha de la 0200) más la
terminal y el RRN, sin campo 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from till.host import iso
from till.host.enlace import Transporte
from till.host.iso import MTI


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DatosTerminal:
    terminal: str  # campo 41, 8 caracteres
    comercio: str  # campo 42, 15 caracteres
    ubicacion: str  # campo 43, 40 caracteres
    adquirente: str = "12345"  # campo 32

    def __post_init__(self) -> None:
        if len(self.terminal) != 8:
            raise ValueError("el identificador de terminal debe tener 8 caracteres")


@dataclass
class RespuestaHost:
    codigo: str
    autorizacion: str | None
    saldo: int | None
    mti: str

    @property
    def aprobada(self) -> bool:
        return self.codigo == iso.Respuesta.APROBADA

    @property
    def descripcion(self) -> str:
        return iso.DESCRIPCION_RESPUESTA.get(self.codigo, "código " + self.codigo)


class ClienteHost:
    def __init__(self, transporte: Transporte, timeout: float = 2.0):
        self.transporte = transporte
        self.timeout = timeout

    def financiera(
        self,
        dt: DatosTerminal,
        tipo: str,
        monto: int,
        stan: str,
        f7: str,
        rrn: str,
        momento: datetime,
        pan: str,
        campo55: str,
        destino: str | None = None,
    ) -> dict[str, str]:
        h12, f13 = iso.campos_12_13(momento)
        msg = {
            "t": MTI.SOLICITUD,
            "2": pan,
            "3": iso.PROCESO[tipo],
            "4": iso.monto(monto),
            "7": f7,
            "11": stan,
            "12": h12,
            "13": f13,
            "32": dt.adquirente,
            "37": rrn,
            "41": dt.terminal,
            "42": dt.comercio.ljust(15)[:15],
            "43": dt.ubicacion.ljust(40)[:40],
            "49": iso.MONEDA_MXN,
            "55": campo55,
        }
        if destino:
            msg["103"] = destino[:28]
        return msg

    async def solicitar(self, msg: dict[str, str]) -> RespuestaHost:
        """Envía una 0200. Propaga ``TiempoAgotadoHost`` / ``HostNoDisponible``."""
        resp = iso.decodificar(await self.transporte.enviar(iso.codificar(msg), self.timeout))
        if resp.get("t") != MTI.RESPUESTA or resp.get("11") != msg["11"]:
            raise ValueError("respuesta que no corresponde a la solicitud")
        return RespuestaHost(resp["39"], resp.get("38"), iso.leer_campo_54(resp.get("54")), resp["t"])

    def reversa(
        self,
        dt: DatosTerminal,
        original: dict[str, Any],
        stan: str,
        momento: datetime,
        repeticion: bool,
    ) -> dict[str, str]:
        """``original``: tipo, monto, stan, f7, rrn, reemplazo y motivo de la operación a revertir."""
        msg = {
            "t": MTI.REVERSA_REPETICION if repeticion else MTI.REVERSA,
            "3": iso.PROCESO[original["tipo"]],
            "4": iso.monto(int(original["monto"])),
            "7": iso.campo_7(momento),
            "11": stan,
            "32": dt.adquirente,
            "37": original["rrn"],
            "39": original["motivo"],
            "41": dt.terminal,
            "42": dt.comercio.ljust(15)[:15],
            "49": iso.MONEDA_MXN,
            "90": iso.campo_90(MTI.SOLICITUD, original["stan"], original["f7"], dt.adquirente),
        }
        if int(original.get("reemplazo", 0)) > 0:
            msg["95"] = iso.campo_95(int(original["reemplazo"]))
        return msg

    async def revertir(self, msg: dict[str, str]) -> bool:
        """``True`` sólo si llega el 0430 que acusa la reversa."""
        resp = iso.decodificar(await self.transporte.enviar(iso.codificar(msg), self.timeout))
        return resp.get("t") == MTI.ACUSE_REVERSA and resp.get("39") == iso.Respuesta.APROBADA

    async def eco(self, dt: DatosTerminal, stan: str, momento: datetime | None = None) -> bool:
        momento = momento or ahora_utc()
        msg = {"t": MTI.RED, "7": iso.campo_7(momento), "11": stan, "70": "301"}
        resp = iso.decodificar(await self.transporte.enviar(iso.codificar(msg), self.timeout))
        return resp.get("t") == MTI.RESPUESTA_RED and resp.get("39") == iso.Respuesta.APROBADA
