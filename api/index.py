"""Entrada local de la API y de la demo web: la API de Till bajo /api y el front construido.

La demo publicada no usa este archivo: es un sitio estático y la página corre la misma API en el navegador
(Pyodide, ver web/src/motor/). Este servidor queda para trabajar en local y para depurar la API con
VITE_TILL_MOTOR=servidor; sirve web/dist con los mismos encabezados de seguridad que vercel.json:

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

# Los mismos valores que vercel.json (una prueba verifica que coincidan). 'wasm-unsafe-eval' es lo único
# que pide Pyodide: compilar WebAssembly; no habilita eval() de JavaScript.
CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
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
