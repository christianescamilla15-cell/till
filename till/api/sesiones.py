"""Una red simulada por visitante.

La demo pública la usan varias personas a la vez: si todas compartieran un solo
cajero, la falla que una arma la dispararía otra. Cada navegador manda un
identificador propio en el encabezado ``X-Till-Sesion`` y recibe su propia red
(banco, cajero, bitácora y libro mayor). Sin el encabezado se usa la red
principal, como antes.

Las redes de sesión viven en memoria y su base SQLite en un directorio de
trabajo (``/tmp`` en un entorno de sólo lectura como Vercel). Hay un tope de
sesiones y se descartan las inactivas: es una demo, no un sistema de cuentas.
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from till.red import Red, construir_red

PATRON_SESION = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass
class Entorno:
    """Lo que una sesión necesita: su red y la última simulación de flota que corrió."""

    red: Red
    flota: dict[str, Any] | None = None
    usado: float = field(default_factory=time.monotonic)


def borrar_base(ruta: str | Path) -> None:
    for sufijo in ("", "-wal", "-shm"):
        Path(str(ruta) + sufijo).unlink(missing_ok=True)


class Sesiones:
    def __init__(self, directorio: str | Path, cajeros: int = 1, maximo: int = 50, inactividad: float = 1800.0):
        self.directorio = Path(directorio)
        self.cajeros = cajeros
        self.maximo = maximo
        self.inactividad = inactividad
        self._entornos: OrderedDict[str, Entorno] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entornos)

    def obtener(self, sesion: str) -> Entorno:
        if not PATRON_SESION.match(sesion):
            raise ValueError("identificador de sesión inválido (8 a 64 caracteres: letras, dígitos, - o _)")
        ent = self._entornos.get(sesion)
        if ent is None:
            self.purgar()
            while len(self._entornos) >= self.maximo:  # la menos reciente cede su lugar
                self._descartar(next(iter(self._entornos)))
            ruta = self.directorio / f"sesion-{sesion}.sqlite3"
            borrar_base(ruta)
            ent = Entorno(construir_red(ruta, cajeros=self.cajeros))
            self._entornos[sesion] = ent
        ent.usado = time.monotonic()
        self._entornos.move_to_end(sesion)
        return ent

    def entornos(self) -> list[Entorno]:
        return list(self._entornos.values())

    def purgar(self) -> int:
        limite = time.monotonic() - self.inactividad
        viejas = [s for s, e in self._entornos.items() if e.usado < limite]
        for s in viejas:
            self._descartar(s)
        return len(viejas)

    def _descartar(self, sesion: str) -> None:
        ent = self._entornos.pop(sesion)
        ent.red.cerrar()
        borrar_base(ent.red.ruta_db)

    def cerrar(self) -> None:
        for s in list(self._entornos):
            self._descartar(s)
