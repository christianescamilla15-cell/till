"""Utilidades comunes de las pruebas.

No usamos pytest-asyncio: cada prueba asíncrona corre con ``asyncio.run``.
"""

import asyncio

import pytest


@pytest.fixture
def correr():
    return asyncio.run
