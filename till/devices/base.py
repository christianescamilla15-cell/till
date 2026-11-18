"""Modelo de comandos, terminaciones y eventos de los dispositivos.

Inspirado en XFS4IoT (no compatible): cada dispositivo expone comandos con
nombre ``Interfaz.Comando`` (``CashDispenser.Dispense``, ``CardReader.ReadRawData``,
...), cada comando termina con una *terminación* que trae un código
(``success``, ``hardwareError``, ``timeOut``...) y, mientras trabaja, el
dispositivo publica *eventos* (``CashDispenser.ItemsPresentedEvent``...).

La interfaz es asíncrona y todos los dispositivos aceptan inyección de fallas.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CodigoTerminacion(StrEnum):
    EXITO = "success"
    ERROR_COMANDO = "commandErrorCode"
    TIEMPO_AGOTADO = "timeOut"
    ERROR_HARDWARE = "hardwareError"
    NO_ENCENDIDO = "deviceNotReady"
    NO_SOPORTADO = "unsupportedCommand"


@dataclass(frozen=True)
class Terminacion:
    comando: str
    id_solicitud: int
    codigo: CodigoTerminacion
    error: str | None = None
    datos: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.codigo is CodigoTerminacion.EXITO


@dataclass(frozen=True)
class Evento:
    dispositivo: str
    nombre: str
    datos: dict[str, Any]
    id_solicitud: int | None
    momento: float


class CorteDeEnergia(BaseException):
    """Simula que el proceso del cajero muere a media operación.

    Hereda de ``BaseException`` a propósito: ningún ``except Exception`` de la
    máquina de estados la atrapa, igual que no se puede "atrapar" un apagón.
    Lo que quede confirmado en la bitácora es lo único que sobrevive.
    """


class Falla(StrEnum):
    ATASCO = "atasco"
    ENTREGA_PARCIAL = "entrega_parcial"
    TIEMPO_AGOTADO = "tiempo_agotado"
    CORTE_ENERGIA = "corte_energia"
    NO_RETIRADO = "efectivo_no_retirado"


@dataclass
class FallaProgramada:
    falla: Falla
    comando: str
    parametros: dict[str, Any] = field(default_factory=dict)
    restantes: int = 1

    def como_dict(self) -> dict[str, Any]:
        return {
            "falla": self.falla.value,
            "comando": self.comando,
            "parametros": dict(self.parametros),
            "restantes": self.restantes,
        }


class FallaNoSoportada(ValueError):
    pass


class InyectorFallas:
    """Cola de fallas programadas por comando, más un modo aleatorio opcional."""

    def __init__(self, soportadas: dict[str, set[Falla]]):
        self.soportadas = soportadas
        self._cola: list[FallaProgramada] = []
        self._prob = 0.0
        self._rng: random.Random | None = None
        self._aleatorias: dict[str, list[tuple[Falla, dict[str, Any]]]] = {}

    def programar(self, falla: Falla | str, comando: str, veces: int = 1, **parametros: Any) -> FallaProgramada:
        falla = Falla(falla)
        if comando not in self.soportadas:
            raise FallaNoSoportada(f"el comando {comando!r} no existe en este dispositivo")
        if falla not in self.soportadas[comando]:
            raise FallaNoSoportada(f"la falla {falla.value!r} no aplica a {comando!r}")
        fp = FallaProgramada(falla, comando, dict(parametros), max(1, int(veces)))
        self._cola.append(fp)
        return fp

    def tomar(self, comando: str) -> FallaProgramada | None:
        for fp in self._cola:
            if fp.comando == comando:
                fp.restantes -= 1
                if fp.restantes <= 0:
                    self._cola.remove(fp)
                return FallaProgramada(fp.falla, fp.comando, dict(fp.parametros), 1)
        if self._rng is not None and comando in self._aleatorias and self._rng.random() < self._prob:
            falla, params = self._rng.choice(self._aleatorias[comando])
            return FallaProgramada(falla, comando, dict(params), 1)
        return None

    def aleatorio(
        self,
        probabilidad: float,
        rng: random.Random,
        fallas: dict[str, list[tuple[Falla, dict[str, Any]]]],
    ) -> None:
        """Activa fallas aleatorias por comando (para la muestra de flota)."""
        self._prob = probabilidad
        self._rng = rng
        self._aleatorias = fallas

    def pendientes(self) -> list[dict[str, Any]]:
        return [fp.como_dict() for fp in self._cola]

    def limpiar(self) -> None:
        self._cola.clear()
        self._prob = 0.0
        self._rng = None


Manejador = Callable[[int, dict[str, Any], FallaProgramada | None], Awaitable[Terminacion]]


class Dispositivo:
    """Base de los dispositivos simulados."""

    clase: str = "Common"
    FALLAS_SOPORTADAS: dict[str, set[Falla]] = {}

    def __init__(self, ident: str, latencia: float = 0.0):
        self.ident = ident
        self.latencia = latencia
        self.encendido = True
        self.fallas = InyectorFallas(self.FALLAS_SOPORTADAS)
        self._ids = itertools.count(1)
        self._suscriptores: list[asyncio.Queue[Evento]] = []
        self.eventos_recientes: deque[Evento] = deque(maxlen=200)

    # -- eventos ---------------------------------------------------------
    def suscribir(self) -> asyncio.Queue[Evento]:
        q: asyncio.Queue[Evento] = asyncio.Queue(maxsize=1000)
        self._suscriptores.append(q)
        return q

    def _emitir(self, nombre: str, datos: dict[str, Any], id_solicitud: int | None = None) -> None:
        ev = Evento(self.ident, nombre, datos, id_solicitud, time.time())
        self.eventos_recientes.append(ev)
        for q in self._suscriptores:
            if not q.full():
                q.put_nowait(ev)

    # -- comandos --------------------------------------------------------
    @property
    def comandos(self) -> list[str]:
        return sorted(n[5:].replace("_", ".", 1) for n in dir(self) if n.startswith("_cmd_"))

    async def ejecutar(self, comando: str, **datos: Any) -> Terminacion:
        id_sol = next(self._ids)
        manejador: Manejador | None = getattr(self, "_cmd_" + comando.replace(".", "_"), None)
        if manejador is None:
            return Terminacion(comando, id_sol, CodigoTerminacion.NO_SOPORTADO, "unsupportedCommand")
        if not self.encendido:
            return Terminacion(comando, id_sol, CodigoTerminacion.NO_ENCENDIDO, "powerOff")
        falla = self.fallas.tomar(comando) if comando in self.FALLAS_SOPORTADAS else None
        if self.latencia:
            await asyncio.sleep(self.latencia)
        return await manejador(id_sol, datos, falla)

    def _apagar(self) -> None:
        self.encendido = False
        self._emitir("Common.PowerLossEvent", {})

    async def encender(self) -> dict[str, Any]:
        """Rutina de arranque del dispositivo tras un corte. Subclases la extienden."""
        self.encendido = True
        self._emitir("Common.PowerRestoredEvent", {})
        return {}

    async def _cmd_Common_Status(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        return Terminacion("Common.Status", id_sol, CodigoTerminacion.EXITO, datos=self.estado())

    def estado(self) -> dict[str, Any]:
        return {"dispositivo": self.ident, "clase": self.clase, "encendido": self.encendido}
