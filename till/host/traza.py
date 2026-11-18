"""Traza de los mensajes ISO 8583 que viajan entre el cajero y el banco.

Envuelve cualquier transporte y anota cada solicitud con su respuesta (o con lo
que pasó en su lugar: tiempo agotado, host caído, corte de energía). Sirve para
que una persona *vea* el diálogo con el banco; no es la bitácora transaccional
(ésa es la base SQLite) y vive sólo en memoria, con un tope de entradas.

Nada sensible entra a la traza: el PAN se guarda enmascarado (6+4) y el campo 55
(datos del chip) se descarta, con :func:`till.host.iso.sin_datos_sensibles`.
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from till.devices.base import CorteDeEnergia
from till.host import iso
from till.host.enlace import HostNoDisponible, TiempoAgotadoHost, Transporte


def _limpio(raw: bytes) -> dict[str, str]:
    try:
        return iso.sin_datos_sensibles(iso.decodificar(raw))
    except Exception:  # un mensaje que no se pudo decodificar no tumba al cajero
        return {"t": iso.mti_de(raw) if len(raw) >= 4 else "????"}


class TrazaIso:
    """Últimos ``maximo`` intercambios, del más viejo al más nuevo."""

    def __init__(self, maximo: int = 500):
        self._entradas: deque[dict[str, Any]] = deque(maxlen=maximo)
        self._ids = itertools.count(1)

    def anotar(self, entrada: dict[str, Any]) -> dict[str, Any]:
        entrada["id"] = next(self._ids)
        self._entradas.append(entrada)
        return entrada

    def listar(self, terminal: str | None = None, rrn: str | None = None, limite: int = 100) -> list[dict[str, Any]]:
        r = [
            e for e in self._entradas
            if (terminal is None or e["terminal"] == terminal) and (rrn is None or e["rrn"] == rrn)
        ]
        return r[-limite:]

    def limpiar(self) -> None:
        self._entradas.clear()


class TransporteConTraza:
    """Transporte que anota en una :class:`TrazaIso` y delega en otro transporte."""

    def __init__(self, interno: Transporte, traza: TrazaIso):
        self.interno = interno
        self.traza = traza

    async def enviar(self, raw: bytes, timeout: float) -> bytes:
        solicitud = _limpio(raw)
        entrada: dict[str, Any] = {
            "momento": datetime.now(timezone.utc).isoformat(),
            "mti": solicitud.get("t"),
            "terminal": solicitud.get("41"),
            "rrn": solicitud.get("37"),
            "solicitud": solicitud,
            "respuesta": None,
            "resultado": "respuesta",
            "error": None,
        }
        t0 = time.perf_counter()
        try:
            resp = await self.interno.enviar(raw, timeout)
            entrada["respuesta"] = _limpio(resp)
            return resp
        except TiempoAgotadoHost as e:
            entrada["resultado"] = "tiempo_agotado"
            entrada["error"] = str(e)
            perdida = getattr(e, "respuesta_perdida", None)
            if perdida:  # el banco sí contestó, pero la respuesta no llegó al cajero
                entrada["respuesta_perdida"] = _limpio(perdida)
            raise
        except HostNoDisponible as e:
            entrada["resultado"] = "host_caido"
            entrada["error"] = str(e)
            raise
        except CorteDeEnergia as e:
            entrada["resultado"] = "corte_energia"
            entrada["error"] = str(e)
            raise
        finally:
            entrada["duracion_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            self.traza.anotar(entrada)
