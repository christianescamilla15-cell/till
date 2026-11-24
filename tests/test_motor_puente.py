"""El puente ASGI del motor de la demo (web/src/motor/puente.py) con la app real, sin navegador.

En la demo publicada, el worker carga Pyodide, copia el código de till a /app y le pasa cada solicitud a
``llamar``. Aquí se carga ese MISMO archivo en CPython y se le hacen las llamadas que hace la página.
La versión que corre de verdad en Pyodide (Python 3.14 y los paquetes de su lockfile) la cubre
``web/scripts/humo_motor.mjs`` en el job «motor» del CI.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
PUENTE = RAIZ / "web" / "src" / "motor" / "puente.py"
PAN = "4111111111111111"


@pytest.fixture
def puente(tmp_path, monkeypatch):
    rutas = list(sys.path)
    monkeypatch.setenv("TILL_DATOS", str(tmp_path))  # primero: así se restaura el valor original al terminar
    spec = importlib.util.spec_from_file_location("till_puente_prueba", PUENTE)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)  # fija TILL_DATOS=/tmp/till y agrega /app a sys.path, como en el worker
    sys.path[:] = rutas
    monkeypatch.setenv("TILL_DATOS", str(tmp_path))  # y otra vez: en las pruebas, las bases van al temporal
    return modulo


async def _llamar(puente, metodo, ruta, cuerpo=None, **encabezados):
    enc = {"X-Till-Sesion": "sesion-puente"} | ({"Content-Type": "application/json"} if cuerpo is not None else {}) | encabezados
    texto = await puente.llamar(metodo, ruta, json.dumps(enc), json.dumps(cuerpo) if cuerpo is not None else "")
    estado, respuesta = json.loads(texto)
    return estado, json.loads(respuesta) if respuesta else None


def test_el_puente_arranca_la_app_y_atiende_un_retiro_completo(puente, tmp_path):
    async def flujo():
        await puente.arrancar()
        try:
            salud = await _llamar(puente, "GET", "/api/salud")
            retiro = await _llamar(puente, "POST", "/api/cajeros/TILL0001/operaciones",
                                   {"tipo": "retiro", "pan": PAN, "monto_pesos": 2500}, **{"Idempotency-Key": "p-1"})
            detalle = await _llamar(puente, "GET", f"/api/operaciones/{retiro[1]['tx_id']}")
            cuadre = await _llamar(puente, "GET", "/api/cajeros/TILL0001/cuadre")
            consulta = await _llamar(puente, "GET", "/api/cajeros/TILL0001/operaciones?limite=1")
            assert (tmp_path / "sesiones" / "sesion-sesion-puente.sqlite3").exists()  # la base de la sesión, donde se pidió
            return salud, retiro, detalle, cuadre, consulta
        finally:
            await puente._ciclo.__aexit__(None, None, None)

    salud, retiro, detalle, cuadre, consulta = asyncio.run(flujo())
    assert salud == (200, salud[1]) and salud[1]["estado"] == "ok" and salud[1]["libro_balanceado"]
    assert retiro[0] == 200 and retiro[1]["estado"] == "CONFIRMADA" and retiro[1]["entregado"] == 250_000
    assert [m["mti"] for m in detalle[1]["mensajes"]] == ["0200"] and "MONTO $2,500.00" in detalle[1]["comprobante"]
    assert PAN not in json.dumps(detalle[1])
    assert cuadre[1]["estado"] == "CUADRA"
    assert consulta[0] == 200 and [x["id"] for x in consulta[1]] == [retiro[1]["tx_id"]]  # la cadena de consulta llega
    assert not (tmp_path / "sesiones" / "sesion-sesion-puente.sqlite3").exists()  # al cerrar, la sesión se descarta


def test_el_puente_entrega_los_errores_de_la_api_tal_cual(puente):
    async def flujo():
        await puente.arrancar()
        try:
            return (
                await _llamar(puente, "GET", "/api/cajeros/NOEXISTE"),
                await _llamar(puente, "POST", "/api/cajeros/TILL0001/operaciones", {"tipo": "retiro", "pan": PAN, "monto_pesos": 100}),
                await _llamar(puente, "GET", "/api/no-existe"),
                await _llamar(puente, "GET", "/api/docs"),  # sin Swagger en la demo: la CSP no permite su CDN
            )
        finally:
            await puente._ciclo.__aexit__(None, None, None)

    no_existe, sin_llave, ruta_desconocida, docs = asyncio.run(flujo())
    assert no_existe[0] == 404 and sin_llave[0] == 422 and ruta_desconocida[0] == 404 and docs[0] == 404


def test_el_puente_es_el_que_carga_el_worker():
    """El worker importa puente.py como texto y llama exactamente arrancar() y llamar(metodo, ruta, encabezados, cuerpo)."""
    worker = (RAIZ / "web" / "src" / "motor" / "motor.worker.js").read_text(encoding="utf-8")
    assert "import PUENTE from './puente.py?raw'" in worker
    assert "await arrancar()" in worker and "py.globals.get('llamar')" in worker
    assert "llamar(metodo, ruta, JSON.stringify(encabezados || {}), cuerpo ?? '')" in worker
