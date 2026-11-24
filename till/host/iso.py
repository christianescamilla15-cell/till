"""Mensajes ISO 8583 (versión 1987, codificación ASCII) sobre ``pyiso8583``.

Mensajes que usa Till:

* 0200 / 0210  solicitud financiera y su respuesta.
* 0420 / 0430  aviso de reversa y su acuse (0421 es la repetición del aviso).
* 0800 / 0810  administración de red (eco).

Campos principales: 2 PAN (sólo viaja, nunca se persiste), 3 código de proceso,
4 monto en centavos, 7 fecha y hora de transmisión (UTC), 11 STAN, 12/13 hora y
fecha locales, 32 adquirente, 37 RRN, 38 autorización, 39 respuesta (o motivo
en la reversa), 41 terminal, 42 comercio, 43 ubicación, 49 moneda (484 = MXN),
54 saldo, 55 datos del chip, 70 código de red, 90 datos originales, 95 montos
de reemplazo (reversa parcial), 103 cuenta destino.
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any

import iso8583
from iso8583.specs import default_ascii

try:  # zona horaria del sitio para los campos 12 y 13
    from zoneinfo import ZoneInfo

    ZONA_LOCAL = ZoneInfo("America/Mexico_City")
except Exception:  # pragma: no cover - sin base de zonas horarias
    ZONA_LOCAL = timezone.utc

ESPEC: dict[str, Any] = copy.deepcopy(default_ascii)


class MTI:
    SOLICITUD = "0200"
    RESPUESTA = "0210"
    REVERSA = "0420"
    REVERSA_REPETICION = "0421"
    ACUSE_REVERSA = "0430"
    RED = "0800"
    RESPUESTA_RED = "0810"


MONEDA_MXN = "484"

PROCESO: dict[str, str] = {
    "retiro": "010000",
    "deposito": "210000",
    "pago_credito": "500000",
    # Remesa pagada en efectivo. En una red real el código se pacta con el switch.
    "remesa": "400000",
}
TIPO_POR_PROCESO: dict[str, str] = {v[:2]: k for k, v in PROCESO.items()}


class Respuesta:
    APROBADA = "00"
    NO_HONRAR = "05"
    TRANSACCION_INVALIDA = "12"
    MONTO_INVALIDO = "13"
    TARJETA_INVALIDA = "14"
    FONDOS_INSUFICIENTES = "51"
    EXCEDE_LIMITE = "61"
    EMISOR_NO_DISPONIBLE = "91"


class MotivoReversa:
    """Motivo de la reversa en el campo 39 de la 0420 (convención del simulador)."""

    SIN_RESPUESTA = "68"
    FALLA_DISPOSITIVO = "22"
    PARCIAL = "32"


DESCRIPCION_RESPUESTA = {
    "00": "aprobada",
    "05": "no honrar",
    "12": "transacción inválida",
    "13": "monto inválido",
    "14": "tarjeta inválida",
    "51": "fondos insuficientes",
    "61": "excede el límite de retiro",
    "91": "emisor no disponible",
}


def codificar(msg: dict[str, str]) -> bytes:
    raw, _ = iso8583.encode(dict(msg), ESPEC)
    return bytes(raw)


def decodificar(raw: bytes) -> dict[str, str]:
    dec, _ = iso8583.decode(bytes(raw), ESPEC)
    return dec


def mti_de(raw: bytes) -> str:
    return bytes(raw[:4]).decode("ascii")


# -- campos ---------------------------------------------------------------
def campo_7(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%m%d%H%M%S")


def campos_12_13(dt: datetime) -> tuple[str, str]:
    local = dt.astimezone(ZONA_LOCAL)
    return local.strftime("%H%M%S"), local.strftime("%m%d")


def rrn(dt: datetime, stan: str) -> str:
    """Retrieval Reference Number: año (1) + día juliano (3) + hora (2) + STAN (6)."""
    u = dt.astimezone(timezone.utc)
    return f"{u.year % 10}{u.timetuple().tm_yday:03d}{u.hour:02d}{stan}"


MONTO_MAXIMO = 10**12 - 1  # campo 4: 12 dígitos en centavos


def monto(centavos: int) -> str:
    if centavos < 0 or centavos > MONTO_MAXIMO:
        raise ValueError("monto fuera de rango para el campo 4")
    return f"{centavos:012d}"


def campo_90(mti_original: str, stan: str, f7: str, adquirente: str) -> str:
    return f"{mti_original}{stan}{f7}{int(adquirente):011d}{0:011d}"


def leer_campo_90(valor: str) -> dict[str, str]:
    return {"mti": valor[0:4], "stan": valor[4:10], "f7": valor[10:20], "adquirente": valor[20:31]}


def campo_95(reemplazo_centavos: int) -> str:
    """Montos de reemplazo: lo que sí se completó (12 + 12 + 9 + 9 = 42)."""
    return f"{reemplazo_centavos:012d}{reemplazo_centavos:012d}{0:09d}{0:09d}"


def leer_campo_95(valor: str | None) -> int:
    return int(valor[:12]) if valor else 0


def campo_54(saldo_centavos: int) -> str:
    """Saldo disponible: tipo de cuenta, tipo de monto 02, moneda, signo, monto."""
    signo = "C" if saldo_centavos >= 0 else "D"
    return f"0002{MONEDA_MXN}{signo}{abs(saldo_centavos):012d}"


def leer_campo_54(valor: str | None) -> int | None:
    if not valor or len(valor) < 20:
        return None
    n = int(valor[8:20])
    return n if valor[7] == "C" else -n


def sin_datos_sensibles(msg: dict[str, str]) -> dict[str, str]:
    """Copia apta para bitácoras: PAN enmascarado y sin datos del chip."""
    limpio = {k: v for k, v in msg.items() if k not in ("55", "52", "35")}
    if "2" in limpio:
        pan = limpio["2"]
        limpio["2"] = pan[:6] + "*" * (len(pan) - 10) + pan[-4:]
    return limpio


# -- marco TCP: 2 bytes de longitud (big endian) + mensaje -----------------
def enmarcar(raw: bytes) -> bytes:
    if len(raw) > 0xFFFF:
        raise ValueError("mensaje demasiado largo")
    return len(raw).to_bytes(2, "big") + raw


async def leer_marco(lector: asyncio.StreamReader) -> bytes:
    cab = await lector.readexactly(2)
    return await lector.readexactly(int.from_bytes(cab, "big"))
