"""Tokenización de tarjetas y criptograma EMV simulado.

Regla: el PAN nunca se guarda en claro. Vive sólo en memoria mientras dura la
sesión de tarjeta (lo necesita el campo 2 de la 0200) y en todo lo persistido
se usa un token con llave (HMAC-SHA256) y el PAN enmascarado.

En producción la tokenización la hace una bóveda de tokens / HSM, el PIN viaja
cifrado bajo DUKPT desde un EPP certificado PCI PTS y el criptograma EMV usa
llaves derivadas por tarjeta (EMV Book 2). Aquí todo eso es una simulación con
HMAC para que el flujo y las responsabilidades sean visibles, no para proteger
datos reales: Till sólo acepta PANs de prueba públicos.
"""

from __future__ import annotations

import hashlib
import hmac
import os

# PANs de prueba públicos (documentados por las marcas para ambientes de prueba).
PANS_DE_PRUEBA: frozenset[str] = frozenset(
    {
        "4111111111111111",
        "4012888888881881",
        "4000056655665556",
        "5555555555554444",
        "5105105105105100",
        "378282246310005",
    }
)

_LLAVE_DEV_TOKEN = b"till-dev-solo-pruebas-token"
_LLAVE_DEV_EMISOR = b"till-dev-solo-pruebas-emisor"


def luhn_valido(pan: str) -> bool:
    if not pan.isdigit() or not 12 <= len(pan) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(pan)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def es_pan_de_prueba(pan: str) -> bool:
    return pan in PANS_DE_PRUEBA and luhn_valido(pan)


def enmascarar(pan: str) -> str:
    """Primeros 6 y últimos 4 (lo máximo que PCI DSS permite mostrar)."""
    return pan[:6] + "*" * (len(pan) - 10) + pan[-4:]


class Tokenizador:
    """Token determinista con llave: el mismo PAN produce el mismo token."""

    def __init__(self, llave: bytes | None = None):
        env = os.environ.get("TILL_LLAVE_TOKEN")
        self._llave = llave or (env.encode() if env else _LLAVE_DEV_TOKEN)

    def token(self, pan: str) -> str:
        mac = hmac.new(self._llave, pan.encode(), hashlib.sha256).hexdigest()
        return "tok_" + mac[:24]


def llave_tarjeta(pan: str, llave_emisor: bytes = _LLAVE_DEV_EMISOR) -> bytes:
    """Llave de la tarjeta derivada del PAN (análogo simplificado a EMV)."""
    return hmac.new(llave_emisor, pan.encode(), hashlib.sha256).digest()


def arqc(llave: bytes, monto_centavos: int, stan: str, terminal: str, atc: int) -> str:
    """Criptograma de solicitud de autorización (simulado): 8 bytes en hex."""
    datos = f"{monto_centavos:012d}|{stan}|{terminal}|{atc:04x}".encode()
    return hmac.new(llave, datos, hashlib.sha256).hexdigest()[:16].upper()


def tlv_icc(criptograma: str, atc: int) -> str:
    """Campo 55 mínimo en TLV (hex): 9F26 criptograma y 9F36 contador ATC."""
    return f"9F2608{criptograma}9F3602{atc:04X}"


def leer_tlv_icc(campo55: str) -> tuple[str, int]:
    """Inverso de :func:`tlv_icc` (sólo para las dos etiquetas que usamos)."""
    if not campo55.startswith("9F2608") or campo55[22:28] != "9F3602":
        raise ValueError("campo 55 con formato inesperado")
    return campo55[6:22], int(campo55[28:32], 16)
