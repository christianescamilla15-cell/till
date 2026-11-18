"""Puente entre la página y la API de Till dentro de Pyodide.

La página no habla HTTP con un servidor: el worker le pasa cada solicitud a ``llamar`` y ésta la
entrega a la misma aplicación FastAPI que corre en local y en las pruebas, como una llamada ASGI.
Las bases SQLite van al sistema de archivos en memoria del worker (``/tmp/till``).
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")
os.environ["TILL_DATOS"] = "/tmp/till"

from till.api.app import crear_app  # noqa: E402

app = crear_app(prefijo="/api", documentacion=False, max_sesiones=8)
_ciclo = None


async def arrancar():
    """Corre el arranque de la app (red principal, sesiones y ciclo de fondo de reversas)."""
    global _ciclo
    _ciclo = app.router.lifespan_context(app)
    await _ciclo.__aenter__()


async def llamar(metodo, ruta, encabezados_json, cuerpo):
    """Una solicitud HTTP como llamada ASGI; devuelve ``[estado, texto]`` en JSON."""
    ruta, _, consulta = ruta.partition("?")
    encabezados = json.loads(encabezados_json or "{}")
    alcance = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": metodo,
        "scheme": "https",
        "path": ruta,
        "raw_path": ruta.encode(),
        "query_string": consulta.encode(),
        "root_path": "",
        "headers": [(k.lower().encode("latin-1"), str(v).encode("latin-1")) for k, v in encabezados.items()],
        "client": ("navegador", 0),
        "server": ("till", 443),
    }
    datos = (cuerpo or "").encode()
    entregado = False

    async def recibir():
        nonlocal entregado
        if not entregado:
            entregado = True
            return {"type": "http.request", "body": datos, "more_body": False}
        await asyncio.Event().wait()  # nunca se desconecta: la respuesta siempre termina antes

    estado, partes = 500, []

    async def enviar(mensaje):
        nonlocal estado
        if mensaje["type"] == "http.response.start":
            estado = mensaje["status"]
        elif mensaje["type"] == "http.response.body":
            partes.append(mensaje.get("body", b""))

    await app(alcance, recibir, enviar)
    return json.dumps([estado, b"".join(partes).decode("utf-8")])
