"""Lector de tarjetas EMV simulado.

Sólo acepta PANs de prueba públicos. Al leer la tarjeta entrega un token y el
PAN enmascarado; el PAN en claro queda únicamente en una ``SesionTarjeta`` en
memoria, que se borra al expulsar la tarjeta. Nada de lo que regresa en las
terminaciones o en los eventos trae el PAN completo.
"""

from __future__ import annotations

from typing import Any

from till import seguridad
from till.devices.base import (
    CodigoTerminacion as C,
    Dispositivo,
    Falla,
    FallaProgramada,
    Terminacion,
)


class PanNoPermitido(ValueError):
    pass


class SesionTarjeta:
    """El único lugar donde vive el PAN en claro, y sólo mientras dura la operación."""

    __slots__ = ("_pan", "token", "enmascarado", "vencimiento")

    def __init__(self, pan: str, token: str, enmascarado: str, vencimiento: str):
        self._pan: str | None = pan
        self.token = token
        self.enmascarado = enmascarado
        self.vencimiento = vencimiento

    @property
    def pan(self) -> str:
        if self._pan is None:
            raise RuntimeError("la sesión de tarjeta ya se cerró")
        return self._pan

    @property
    def activa(self) -> bool:
        return self._pan is not None

    def olvidar(self) -> None:
        self._pan = None

    def __repr__(self) -> str:  # nunca imprimir el PAN
        return f"SesionTarjeta({self.enmascarado}, {self.token})"


class LectorTarjetas(Dispositivo):
    clase = "CardReader"
    FALLAS_SOPORTADAS = {
        "CardReader.ReadRawData": {Falla.ATASCO, Falla.TIEMPO_AGOTADO},
        "CardReader.GenerateAC": {Falla.TIEMPO_AGOTADO},
        "CardReader.Eject": {Falla.ATASCO},
    }

    def __init__(self, ident: str, tokenizador: seguridad.Tokenizador, latencia: float = 0.0):
        super().__init__(ident, latencia)
        self.tokenizador = tokenizador
        self._ranura: tuple[str, str] | None = None
        self.sesion: SesionTarjeta | None = None
        self._atc: dict[str, int] = {}  # contador de transacciones del chip, por token
        self.capturadas = 0

    def insertar(self, pan: str, vencimiento: str = "2812") -> None:
        """Acto físico simulado: el cliente inserta su tarjeta."""
        if not seguridad.es_pan_de_prueba(pan):
            raise PanNoPermitido("Till sólo acepta PANs de prueba públicos")
        self._ranura = (pan, vencimiento)
        self._emitir("CardReader.MediaInsertedEvent", {})

    @property
    def con_tarjeta(self) -> bool:
        return self._ranura is not None

    async def _cmd_CardReader_ReadRawData(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CardReader.ReadRawData"
        if self._ranura is None:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "noMedia")
        if falla:
            codigo = C.ERROR_HARDWARE if falla.falla is Falla.ATASCO else C.TIEMPO_AGOTADO
            return Terminacion(cmd, id_sol, codigo, "readError" if falla.falla is Falla.ATASCO else "timeOut")
        pan, venc = self._ranura
        token = self.tokenizador.token(pan)
        self.sesion = SesionTarjeta(pan, token, seguridad.enmascarar(pan), venc)
        return Terminacion(cmd, id_sol, C.EXITO, datos={"token": token, "enmascarado": self.sesion.enmascarado})

    async def _cmd_CardReader_GenerateAC(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CardReader.GenerateAC"
        if self.sesion is None or not self.sesion.activa:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "noMedia")
        if falla:
            return Terminacion(cmd, id_sol, C.TIEMPO_AGOTADO, "timeOut")
        atc = self._atc.get(self.sesion.token, 0) + 1
        self._atc[self.sesion.token] = atc
        llave = seguridad.llave_tarjeta(self.sesion.pan)
        cripto = seguridad.arqc(llave, int(datos["monto"]), str(datos["stan"]), str(datos["terminal"]), atc)
        return Terminacion(cmd, id_sol, C.EXITO, datos={"campo55": seguridad.tlv_icc(cripto, atc)})

    async def _cmd_CardReader_Eject(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CardReader.Eject"
        if self.sesion is not None:
            self.sesion.olvidar()
        self.sesion = None
        if self._ranura is None:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "noMedia")
        self._ranura = None
        if falla:
            self.capturadas += 1
            self._emitir("CardReader.MediaRetainedEvent", {}, id_sol)
            return Terminacion(cmd, id_sol, C.ERROR_HARDWARE, "mediaJam")
        self._emitir("CardReader.MediaRemovedEvent", {}, id_sol)
        return Terminacion(cmd, id_sol, C.EXITO)

    async def encender(self) -> dict[str, Any]:
        if self.sesion is not None:
            self.sesion.olvidar()
            self.sesion = None
        if self._ranura is not None:
            # Tras un corte, la tarjeta que estaba adentro se captura.
            self._ranura = None
            self.capturadas += 1
        return await super().encender()
