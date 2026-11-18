"""Transporte hacia el host: en proceso (con inyección de fallas) o TCP.

El transporte en proceso es el que usan las pruebas y el simulador: entrega
los bytes ISO 8583 al autorizador y permite simular las fallas típicas de un
enlace: solicitud perdida, respuesta perdida, host caído y el proceso del
cajero muriendo justo después de enviar.

El transporte TCP usa el marco clásico de 2 bytes de longitud y sirve para
probar que el mismo cajero habla con un host por red.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from till.devices.base import CorteDeEnergia
from till.host import iso
from till.host.autorizador import HostAutorizador


class TiempoAgotadoHost(Exception):
    """No llegó respuesta a tiempo. No se sabe si el host aplicó la solicitud.

    En el transporte simulado, si el banco sí contestó y la respuesta se perdió,
    ``respuesta_perdida`` trae esos bytes para la traza; el cajero nunca los usa.
    """

    respuesta_perdida: bytes | None = None


class HostNoDisponible(Exception):
    """No se pudo entregar la solicitud (conexión rechazada)."""


class FallaEnlace(StrEnum):
    SOLICITUD_PERDIDA = "solicitud_perdida"
    RESPUESTA_PERDIDA = "respuesta_perdida"
    HOST_CAIDO = "host_caido"
    CORTE_TRAS_ENVIO = "corte_energia_tras_envio"


class Transporte(Protocol):
    async def enviar(self, raw: bytes, timeout: float) -> bytes: ...


@dataclass
class _FallaEnlaceProgramada:
    falla: FallaEnlace
    mti: str | None
    restantes: int


class TransporteEnProceso:
    def __init__(self, host: HostAutorizador, latencia: float = 0.0):
        self.host = host
        self.latencia = latencia
        self._fallas: list[_FallaEnlaceProgramada] = []
        self._prob = 0.0
        self._rng: random.Random | None = None
        self.enviados = 0

    def programar(self, falla: FallaEnlace | str, mti: str | None = None, veces: int = 1) -> None:
        self._fallas.append(_FallaEnlaceProgramada(FallaEnlace(falla), mti, max(1, int(veces))))

    def aleatorio(self, probabilidad: float, rng: random.Random) -> None:
        self._prob, self._rng = probabilidad, rng

    def pendientes(self) -> list[dict]:
        return [{"falla": f.falla.value, "mti": f.mti, "restantes": f.restantes} for f in self._fallas]

    def limpiar(self) -> None:
        self._fallas.clear()
        self._prob, self._rng = 0.0, None

    def _tomar(self, mti: str) -> FallaEnlace | None:
        for f in self._fallas:
            if f.mti is None or f.mti == mti:
                f.restantes -= 1
                if f.restantes <= 0:
                    self._fallas.remove(f)
                return f.falla
        if self._rng is not None and self._rng.random() < self._prob:
            return self._rng.choice([FallaEnlace.SOLICITUD_PERDIDA, FallaEnlace.RESPUESTA_PERDIDA])
        return None

    async def enviar(self, raw: bytes, timeout: float) -> bytes:
        self.enviados += 1
        falla = self._tomar(iso.mti_de(raw))
        if self.latencia:
            await asyncio.sleep(self.latencia)
        if falla is FallaEnlace.HOST_CAIDO:
            raise HostNoDisponible("conexión rechazada por el host")
        if falla is FallaEnlace.SOLICITUD_PERDIDA:
            raise TiempoAgotadoHost("sin respuesta (la solicitud no llegó)")
        try:
            resp = await asyncio.wait_for(self.host.procesar(raw), timeout)
        except asyncio.TimeoutError as e:
            raise TiempoAgotadoHost("sin respuesta a tiempo") from e
        if falla is FallaEnlace.RESPUESTA_PERDIDA:
            e = TiempoAgotadoHost("sin respuesta (el host sí procesó)")
            e.respuesta_perdida = resp  # sólo diagnóstico: el cajero nunca la recibe
            raise e
        if falla is FallaEnlace.CORTE_TRAS_ENVIO:
            raise CorteDeEnergia("corte de energía con la solicitud ya en el host")
        return resp


class ServidorHostTcp:
    """Expone un ``HostAutorizador`` por TCP con marco de 2 bytes."""

    def __init__(self, host: HostAutorizador):
        self.host = host
        self._servidor: asyncio.base_events.Server | None = None

    async def iniciar(self, direccion: str = "127.0.0.1", puerto: int = 0) -> int:
        self._servidor = await asyncio.start_server(self._atender, direccion, puerto)
        return self._servidor.sockets[0].getsockname()[1]

    async def _atender(self, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            while True:
                raw = await iso.leer_marco(lector)
                escritor.write(iso.enmarcar(await self.host.procesar(raw)))
                await escritor.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            escritor.close()

    async def detener(self) -> None:
        if self._servidor:
            self._servidor.close()
            await self._servidor.wait_closed()


class TransporteTcp:
    """Una solicitud a la vez por conexión; si vence el tiempo se cierra la conexión
    para que una respuesta tardía nunca se confunda con la siguiente."""

    def __init__(self, direccion: str, puerto: int):
        self.direccion, self.puerto = direccion, puerto
        self._lector: asyncio.StreamReader | None = None
        self._escritor: asyncio.StreamWriter | None = None
        self._candado = asyncio.Lock()

    async def _conectar(self) -> None:
        if self._escritor is None or self._escritor.is_closing():
            try:
                self._lector, self._escritor = await asyncio.open_connection(self.direccion, self.puerto)
            except OSError as e:
                raise HostNoDisponible(str(e)) from e

    async def enviar(self, raw: bytes, timeout: float) -> bytes:
        async with self._candado:
            await self._conectar()
            assert self._lector and self._escritor
            try:
                self._escritor.write(iso.enmarcar(raw))
                await self._escritor.drain()
                return await asyncio.wait_for(iso.leer_marco(self._lector), timeout)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError) as e:
                await self.cerrar()
                raise TiempoAgotadoHost(str(e) or "sin respuesta") from e

    async def cerrar(self) -> None:
        if self._escritor is not None:
            self._escritor.close()
            try:
                await self._escritor.wait_closed()
            except Exception:
                pass
        self._lector = self._escritor = None
