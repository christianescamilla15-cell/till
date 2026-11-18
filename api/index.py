"""Entrada de la demo web: la API de Till bajo /api.

En Vercel este archivo es la función de Python que atiende /api/* (vercel.json),
la CDN sirve el front estático de web/dist y las bases SQLite van a /tmp/till,
porque el resto del sistema de archivos es de sólo lectura.

En local sirve además el front construido, con los mismos encabezados de
seguridad que vercel.json (CSP incluida), para probarlo tal como se publica:

    npm ci --prefix web && npm run build --prefix web
    .venv/bin/uvicorn api.index:app --port 8000        # http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:  # el paquete till vive en la raíz del repo
    sys.path.insert(0, str(RAIZ))

from starlette.staticfiles import StaticFiles  # noqa: E402

from till.api.app import crear_app  # noqa: E402

# Los mismos valores que vercel.json (una prueba verifica que coincidan).
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'"
)
ENCABEZADOS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

DIST = RAIZ / "web" / "dist"

app = crear_app(prefijo="/api", documentacion=False)


@app.middleware("http")
async def encabezados_de_seguridad(request, call_next):
    respuesta = await call_next(request)
    for nombre, valor in ENCABEZADOS.items():
        respuesta.headers.setdefault(nombre, valor)
    return respuesta


if not os.environ.get("VERCEL") and DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
