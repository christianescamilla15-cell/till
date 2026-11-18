"""Impresora térmica de comprobantes simulada.

Una falla de impresora NO revierte una operación: si el efectivo ya se entregó,
la operación está hecha y el comprobante se puede reimprimir o enviar por
otro medio. Como defensa adicional, la impresora se niega a imprimir algo que
parezca un PAN completo.
"""

from __future__ import annotations

import re

from till.devices.base import (
    CodigoTerminacion as C,
    Dispositivo,
    Falla,
    FallaProgramada,
    Terminacion,
)

_PATRON_PAN = re.compile(r"\d{13,19}")


class ImpresoraComprobantes(Dispositivo):
    clase = "Printer"
    FALLAS_SOPORTADAS = {"Printer.PrintForm": {Falla.ATASCO, Falla.TIEMPO_AGOTADO}}

    def __init__(self, ident: str, papel: int = 2000, latencia: float = 0.0):
        super().__init__(ident, latencia)
        self.papel = papel
        self.comprobantes: list[str] = []

    async def _cmd_Printer_PrintForm(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "Printer.PrintForm"
        lineas = [str(x) for x in datos.get("lineas", [])]
        texto = "\n".join(lineas)
        if _PATRON_PAN.search(texto.replace(" ", "")):
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "panEnClaro")
        if falla:
            codigo = C.ERROR_HARDWARE if falla.falla is Falla.ATASCO else C.TIEMPO_AGOTADO
            return Terminacion(cmd, id_sol, codigo, "paperJam" if falla.falla is Falla.ATASCO else "timeOut")
        if self.papel <= 0:
            return Terminacion(cmd, id_sol, C.ERROR_HARDWARE, "paperOut")
        self.papel -= 1
        self.comprobantes.append(texto)
        if len(self.comprobantes) > 200:
            del self.comprobantes[:100]
        return Terminacion(cmd, id_sol, C.EXITO)

    def estado(self) -> dict:
        e = super().estado()
        e["papel"] = self.papel
        return e
