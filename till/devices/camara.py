"""Cámara (stub).

En la Etapa 1 sólo deja una referencia de evidencia por operación. La
verificación de identidad por visión es parte de la Etapa 2 y, aun entonces,
la IA propone y las reglas autorizan.
"""

from __future__ import annotations

import hashlib

from till.devices.base import (
    CodigoTerminacion as C,
    Dispositivo,
    Falla,
    FallaProgramada,
    Terminacion,
)


class Camara(Dispositivo):
    clase = "Camera"
    FALLAS_SOPORTADAS = {"Camera.TakePicture": {Falla.TIEMPO_AGOTADO}}

    def __init__(self, ident: str, latencia: float = 0.0):
        super().__init__(ident, latencia)
        self.fotos = 0

    async def _cmd_Camera_TakePicture(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        if falla:
            return Terminacion("Camera.TakePicture", id_sol, C.TIEMPO_AGOTADO, "timeOut")
        self.fotos += 1
        ref = str(datos.get("referencia", ""))
        nombre = f"sim://{self.ident}/{self.fotos:06d}.jpg"
        huella = hashlib.sha256(f"{nombre}|{ref}".encode()).hexdigest()
        return Terminacion("Camera.TakePicture", id_sol, C.EXITO, datos={"imagen": nombre, "sha256": huella})
