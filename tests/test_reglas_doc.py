"""docs/reglas.md es la compuerta «una prueba por regla»: esta prueba evita que la tabla mienta.

Cada `tests/...py::prueba` citada debe existir como función de prueba de nivel módulo, sin marca skip/xfail;
cada `e2e/...js::nombre` debe existir como `test('nombre', ...)`; y cada fila de regla debe citar al menos una.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOC = RAIZ / "docs" / "reglas.md"
_REF_PY = re.compile(r"`(tests/[\w/]+\.py)::(test_\w+)`")
_REF_JS = re.compile(r"`(e2e/[\w/.]+\.js)::([^`]+)`")
_FILA_REGLA = re.compile(r"^\|\s*([A-Z]{1,2}\d+)\s*\|")


def _filas() -> list[tuple[str, str]]:
    return [(m.group(1), linea) for linea in DOC.read_text(encoding="utf-8").splitlines() if (m := _FILA_REGLA.match(linea))]


def _pruebas_de(archivo: str) -> dict[str, bool]:
    """{nombre: ¿se salta?} de las funciones de prueba de nivel módulo."""
    arbol = ast.parse((RAIZ / archivo).read_text(encoding="utf-8"))
    pruebas = {}
    for nodo in arbol.body:
        if isinstance(nodo, ast.FunctionDef) and nodo.name.startswith("test_"):
            marcas = {ast.unparse(d) for d in nodo.decorator_list}
            pruebas[nodo.name] = any("skip" in m or "xfail" in m for m in marcas)
    return pruebas


def test_la_tabla_tiene_todas_sus_reglas():
    identificadores = [ident for ident, _ in _filas()]
    assert len(identificadores) >= 80, "la tabla perdió reglas"
    assert len(identificadores) == len(set(identificadores)), "hay identificadores de regla repetidos"


def test_cada_regla_cita_al_menos_una_prueba():
    assert [ident for ident, linea in _filas() if not (_REF_PY.search(linea) or _REF_JS.search(linea))] == []


def test_cada_prueba_citada_existe_y_no_se_salta():
    faltantes, saltadas = [], []
    for archivo, prueba in _REF_PY.findall(DOC.read_text(encoding="utf-8")):
        existentes = _pruebas_de(archivo) if (RAIZ / archivo).is_file() else {}
        if prueba not in existentes:
            faltantes.append(f"{archivo}::{prueba}")
        elif existentes[prueba]:
            saltadas.append(f"{archivo}::{prueba}")
    for archivo, nombre in _REF_JS.findall(DOC.read_text(encoding="utf-8")):
        texto = (RAIZ / archivo).read_text(encoding="utf-8") if (RAIZ / archivo).is_file() else ""
        if not re.search(r"\btest\(\s*(['\"`])" + re.escape(nombre) + r"\1", texto):
            faltantes.append(f"{archivo}::{nombre}")
        if re.search(r"\btest\.(skip|fixme)\(\s*(['\"`])" + re.escape(nombre), texto):
            saltadas.append(f"{archivo}::{nombre}")
    assert faltantes == []
    assert saltadas == []


REQUISITOS = RAIZ / "docs" / "arquitectura" / "requisitos.md"
_FILA_REQUISITO = re.compile(r"^\|\s*(RN?F-\d+)\s*\|(.*)$")


def test_cada_requisito_traza_a_reglas_y_componentes_que_existen():
    """requisito → componente → regla → prueba: las reglas citadas existen en reglas.md y los componentes en el repo."""
    reglas = {ident for ident, _ in _filas()}
    principales = []  # las tablas de requisitos (la matriz de resumen sólo repite requisito → prueba)
    for linea in REQUISITOS.read_text(encoding="utf-8").splitlines():
        if (m := _FILA_REQUISITO.match(linea)) and len(celdas := [c.strip() for c in m.group(2).split("|")]) >= 5:
            principales.append((m.group(1), celdas))
    ids = [ident for ident, _ in principales]
    assert len(ids) >= 20 and len(ids) == len(set(ids))
    cubiertas = set()
    for ident, celdas in principales:
        componentes = re.findall(r"`([^`]+)`", celdas[1])
        citadas = {r.strip() for r in celdas[2].split(",")}
        assert componentes, f"{ident} no cita componentes"
        assert [c for c in componentes if not (RAIZ / c).exists()] == [], ident
        assert citadas <= reglas, f"{ident} cita reglas que no existen: {citadas - reglas}"
        cubiertas |= citadas
    assert len(cubiertas) >= 60, "los requisitos dejaron de trazar a la mayoría de las reglas"
