"""Contrato OpenAPI (N07): versionado en docs/api/openapi.json, al día, completo y el mismo que usa la demo.

Si una ruta cambia y nadie regenera el contrato, la primera prueba falla con la instrucción:
`python3 herramientas/exportar_openapi.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import fastapi
import pytest

from till.api.app import crear_app

RAIZ = Path(__file__).resolve().parents[1]
CONTRATO = RAIZ / "docs" / "api" / "openapi.json"
APP = crear_app()


def _fastapi_fijado() -> str:
    texto = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
    return re.search(r"^fastapi==([\w.]+)", texto, re.M).group(1)


def _proyeccion(spec: dict) -> dict:
    """Lo que define el contrato sin el estilo del generador: operaciones, parámetros, cuerpos y respuestas."""
    salida = {}
    for ruta, ops in spec["paths"].items():
        for metodo, op in ops.items():
            salida[f"{metodo.upper()} {ruta}"] = {
                "parametros": sorted((p["name"], p["in"], p.get("required", False)) for p in op.get("parameters", [])),
                "cuerpo": json.dumps(op.get("requestBody", {}).get("content", {}), sort_keys=True),
                "respuestas": sorted(op["responses"]),
            }
    return salida


def test_el_contrato_versionado_esta_al_dia():
    publicado = json.dumps(APP.openapi(), indent=2, ensure_ascii=False) + "\n"
    versionado = CONTRATO.read_text(encoding="utf-8")
    if fastapi.__version__ == _fastapi_fijado():
        assert versionado == publicado, "docs/api/openapi.json no coincide con la API: corre `python3 herramientas/exportar_openapi.py`"
    else:
        # El job «motor» del CI usa la FastAPI de Pyodide: su generador puede escribir distinto lo mismo.
        assert _proyeccion(json.loads(versionado)) == _proyeccion(APP.openapi())


def _operaciones() -> list[tuple[str, str, dict]]:
    return [(m, r, op) for r, ops in json.loads(CONTRATO.read_text(encoding="utf-8"))["paths"].items() for m, op in ops.items()]


@pytest.mark.parametrize("metodo,ruta,op", _operaciones(), ids=lambda v: v if isinstance(v, str) else "")
def test_cada_operacion_esta_documentada(metodo, ruta, op):
    assert op.get("summary", "").strip() and op.get("description", "").strip(), "falta resumen o descripción (docstring)"
    exitos = {c: r for c, r in op["responses"].items() if c.startswith("2")}
    assert exitos and all(r.get("content", {}).get("application/json", {}).get("schema") for r in exitos.values())
    assert all(r.get("description", "").strip() for r in op["responses"].values())
    for p in op.get("parameters", []):
        assert p.get("schema"), f"el parámetro {p['name']} no declara tipo"
    if "{terminal}" in ruta:
        assert "404" in op["responses"], "una ruta por cajero documenta el 404"


def test_la_demo_monta_el_mismo_contrato_bajo_api():
    def sin_id(nodo):  # operationId y los títulos generados llevan la ruta: es lo único que cambia con el prefijo
        if isinstance(nodo, dict):
            return {k: sin_id(v) for k, v in nodo.items() if k not in ("operationId", "title")}
        return [sin_id(x) for x in nodo] if isinstance(nodo, list) else nodo

    raiz, demo = APP.openapi()["paths"], crear_app(prefijo="/api", documentacion=False).openapi()["paths"]
    assert sin_id(demo) == sin_id({"/api" + r: ops for r, ops in raiz.items()})


def test_el_front_solo_llama_rutas_del_contrato():
    contrato = {f"{m.upper()} {r}" for m, r, _ in _operaciones()}
    llamadas = set()
    for archivo in (RAIZ / "web" / "src").rglob("*.js*"):
        for metodo, ruta in re.findall(r"api\.(get|post|del)\(\s*[`'\"]([^`'\"]+)[`'\"]", archivo.read_text(encoding="utf-8")):
            ruta = re.sub(r"\$\{(TERMINAL|terminal)\}", "{terminal}", ruta.split("?")[0])
            ruta = re.sub(r"\$\{\w+\}", "{tx_id}", ruta)
            llamadas.add(f"{'DELETE' if metodo == 'del' else metodo.upper()} {ruta}")
    assert len(llamadas) >= 10
    assert llamadas - contrato == set()
