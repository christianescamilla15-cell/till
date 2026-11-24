"""Pruebas de arquitectura: quién puede importar a quién.

* Capas del núcleo, de abajo hacia arriba: dinero/seguridad/db → dispositivos y libro mayor → host ISO 8583
  → máquina de estados (core) → cuadre (recon) → armado de la red (red) → flota → API. Nadie importa a una
  capa superior.
* El dominio no conoce la API ni el transporte web: FastAPI, Starlette, Pydantic, uvicorn y httpx sólo
  aparecen en ``till/api``; ni el puente del navegador ni la entrada local entran al dominio.
* El motor de la demo (Pyodide) sólo trae los paquetes que lista ``web/scripts/preparar_motor.mjs``: todo
  lo que importa ``till/`` debe ser biblioteca estándar o uno de ellos, o la demo no arranca.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
TILL = RAIZ / "till"

CAPAS: dict[str, int] = {
    "till": 0,  # sólo __version__
    "till.dinero": 0,
    "till.seguridad": 0,
    "till.db": 0,
    "till.devices": 1,
    "till.ledger": 1,
    "till.host": 2,
    "till.core": 3,
    "till.recon": 4,
    "till.red": 5,
    "till.fleet": 6,
    "till.api": 7,
}
WEB_Y_TRANSPORTE = {"fastapi", "starlette", "pydantic", "pydantic_core", "uvicorn", "httpx", "jinja2", "api"}


def _modulo(ruta: Path) -> str:
    partes = list(ruta.relative_to(RAIZ).with_suffix("").parts)
    if partes[-1] == "__init__":
        partes.pop()
    return ".".join(partes)


def _componente(modulo: str) -> str:
    """El componente (capa) de un módulo de till: till.core.maquina → till.core."""
    partes = modulo.split(".")
    return ".".join(partes[:2]) if len(partes) > 1 else "till"


def _importaciones(ruta: Path) -> set[str]:
    """Módulos que importa un archivo, resolviendo ``from till import x`` a ``till.x`` si x es un módulo."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    paquete = _modulo(ruta) if ruta.name == "__init__.py" else _modulo(ruta).rpartition(".")[0]
    salida: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            salida |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom):
            base = nodo.module or ""
            if nodo.level:
                base = ".".join(paquete.split(".")[: len(paquete.split(".")) - nodo.level + 1] + ([base] if base else []))
            for alias in nodo.names:
                candidato = f"{base}.{alias.name}"
                ruta_candidata = RAIZ / Path(*candidato.split("."))
                es_modulo = ruta_candidata.with_suffix(".py").exists() or (ruta_candidata / "__init__.py").exists()
                salida.add(candidato if es_modulo else base)
    return salida


ARCHIVOS = sorted(TILL.rglob("*.py"))


def test_se_revisaron_todos_los_modulos():
    assert len(ARCHIVOS) >= 30  # una prueba de arquitectura que no mira nada no prueba nada
    assert {_componente(_modulo(a)) for a in ARCHIVOS} == set(CAPAS)


@pytest.mark.parametrize("archivo", ARCHIVOS, ids=lambda a: str(a.relative_to(RAIZ)))
def test_ninguna_capa_importa_a_una_superior(archivo):
    origen = _componente(_modulo(archivo))
    violaciones = [
        m for m in _importaciones(archivo)
        if m.split(".")[0] == "till" and CAPAS[_componente(m)] > CAPAS[origen]
    ]
    assert violaciones == [], f"{origen} (capa {CAPAS[origen]}) importa capas superiores: {violaciones}"


@pytest.mark.parametrize("archivo", [a for a in ARCHIVOS if _componente(_modulo(a)) != "till.api"],
                         ids=lambda a: str(a.relative_to(RAIZ)))
def test_el_dominio_no_conoce_la_api_ni_el_transporte_web(archivo):
    externos = {m.split(".")[0] for m in _importaciones(archivo)}
    assert externos & WEB_Y_TRANSPORTE == set()


def test_el_nucleo_transaccional_no_depende_de_la_flota_ni_del_armado():
    for archivo in (TILL / "core").glob("*.py"):
        assert not {m for m in _importaciones(archivo) if m.startswith(("till.fleet", "till.red", "till.recon", "till.api"))}


def test_el_puente_del_navegador_y_la_entrada_local_solo_entran_por_la_app():
    for ruta in (RAIZ / "web" / "src" / "motor" / "puente.py", RAIZ / "api" / "index.py"):
        de_till = {m for m in _importaciones(ruta) if m.split(".")[0] == "till"}
        assert de_till == {"till.api.app"}, f"{ruta.relative_to(RAIZ)} importa {de_till}"


def _paquetes_del_motor() -> set[str]:
    """Nombres importables de lo que preparar_motor.mjs mete al motor de la demo."""
    texto = (RAIZ / "web" / "scripts" / "preparar_motor.mjs").read_text(encoding="utf-8")
    lista = re.search(r"const PAQUETES = \[(.*?)\]", texto, re.S).group(1)
    nombres = {n.replace("-", "_").lower() for n in re.findall(r"'([^']+)'", lista)}
    fuera = set(re.findall(r"imports: \[([^\]]*)\]", texto))
    for grupo in fuera:
        nombres |= set(re.findall(r"'([^']+)'", grupo))
    return nombres


def test_todo_lo_que_importa_till_existe_en_el_motor_de_la_demo():
    motor = _paquetes_del_motor()
    assert {"fastapi", "pydantic", "iso8583", "tzdata"} <= motor
    faltan = {}
    for archivo in ARCHIVOS + [RAIZ / "web" / "src" / "motor" / "puente.py"]:
        for m in _importaciones(archivo):
            raiz = m.split(".")[0]
            if raiz in ("till", "__future__") or raiz in sys.stdlib_module_names or raiz in motor:
                continue
            faltan.setdefault(raiz, []).append(str(archivo.relative_to(RAIZ)))
    assert faltan == {}, f"importaciones que el motor de Pyodide no trae: {faltan}"


def test_los_modulos_esenciales_del_motor_existen():
    texto = (RAIZ / "web" / "scripts" / "preparar_motor.mjs").read_text(encoding="utf-8")
    esenciales = re.findall(r"'(till/[\w/]+\.py)'", texto)
    assert len(esenciales) >= 4
    assert [m for m in esenciales if not (RAIZ / m).is_file()] == []
