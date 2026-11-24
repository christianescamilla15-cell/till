"""El modelo ArchiMate (docs/arquitectura/till.archimate.xml) se genera del código y no inventa componentes.

La validación contra el XSD oficial de The Open Group la hace `herramientas/validar_archimate.sh` (xmllint) en
el CI; aquí se revisa lo que el XSD no puede: que lo versionado esté al día, que no haya referencias colgadas,
que cada relación sea de un tipo que ArchiMate 3.1 permite entre esos dos elementos y que cada «código» exista.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("archimate_modelo", RAIZ / "herramientas" / "archimate.py")
modelo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(modelo)

TIPOS = {e[0]: e[1] for e in modelo.ELEMENTOS}
MOTIVACION = {"Stakeholder", "Driver", "Assessment", "Goal", "Principle", "Requirement", "Constraint"}
NUCLEO = {"BusinessActor", "BusinessRole", "BusinessService", "BusinessProcess", "BusinessObject", "ApplicationComponent",
          "ApplicationInterface", "ApplicationService", "DataObject", "Node", "SystemSoftware", "Device",
          "TechnologyService", "Artifact", "Path"}

# Lo que ArchiMate 3.1 permite (apéndice B de la especificación), reducido a las parejas que usa este modelo.
PERMITIDAS: dict[str, set[tuple[str, str]]] = {
    "Influence": {(a, b) for a in MOTIVACION for b in MOTIVACION},
    "Realization": {("Principle", "Goal"), ("Requirement", "Principle"), ("Requirement", "Goal"),
                    ("BusinessProcess", "BusinessService"), ("ApplicationComponent", "ApplicationService"),
                    ("DataObject", "BusinessObject"), ("Artifact", "ApplicationComponent"),
                    ("Node", "TechnologyService"), ("SystemSoftware", "TechnologyService")}
    | {(n, m) for n in NUCLEO for m in ("Requirement", "Constraint")},
    "Serving": {("BusinessService", "BusinessRole"), ("BusinessService", "BusinessActor"),
                ("ApplicationService", "BusinessProcess"), ("ApplicationService", "BusinessActor"),
                ("ApplicationComponent", "ApplicationComponent"), ("ApplicationInterface", "ApplicationComponent"),
                ("TechnologyService", "ApplicationComponent")},
    "Assignment": {("BusinessActor", "BusinessRole"), ("Node", "Artifact"), ("SystemSoftware", "Artifact")},
    "Composition": {("ApplicationComponent", "ApplicationInterface"), ("ApplicationComponent", "ApplicationComponent"),
                    ("Node", "SystemSoftware")},
    "Aggregation": {("Node", "Device")} | {("Plateau", n) for n in NUCLEO},
    "Access": {("BusinessProcess", "BusinessObject"), ("ApplicationComponent", "DataObject")},
    "Triggering": {("BusinessProcess", "BusinessProcess"), ("Plateau", "Plateau")},
    "Flow": {("ApplicationComponent", "ApplicationComponent")},
}


def test_lo_versionado_es_lo_que_genera_el_modelo():
    assert modelo.XML.read_text(encoding="utf-8") == modelo.xml(), "corre `python3 herramientas/archimate.py`"
    assert modelo.MD.read_text(encoding="utf-8") == modelo.markdown(), "corre `python3 herramientas/archimate.py`"


def test_identificadores_unicos_y_sin_referencias_colgadas():
    ids = [e[0] for e in modelo.ELEMENTOS] + [r[0] for r in modelo.RELACIONES] + [v[0] for v in modelo.VISTAS]
    assert len(ids) == len(set(ids))
    assert [r for r in modelo.RELACIONES if r[2] not in TIPOS or r[3] not in TIPOS] == []
    assert [e for v in modelo.VISTAS for fila in v[3] for e in fila if e not in TIPOS] == []


def test_cubre_las_cuatro_capas_y_la_migracion_y_todo_aparece_en_una_vista():
    assert set(modelo.CAPA_DE_TIPO[t] for t in TIPOS.values()) == {
        "Motivación", "Negocio", "Aplicación", "Tecnología", "Implementación y migración"}
    en_vistas = {e for v in modelo.VISTAS for fila in v[3] for e in fila}
    assert set(TIPOS) - en_vistas == set()
    relacionados = {r[2] for r in modelo.RELACIONES} | {r[3] for r in modelo.RELACIONES}
    assert set(TIPOS) - relacionados == set(), "elementos sueltos, sin ninguna relación"


@pytest.mark.parametrize("relacion", modelo.RELACIONES, ids=lambda r: f"{r[0]}-{r[1]}-{r[2]}-{r[3]}")
def test_cada_relacion_es_valida_en_archimate(relacion):
    _, tipo, origen, destino, _ = relacion
    if tipo == "Association":  # la asociación se permite entre cualquier par
        return
    assert (TIPOS[origen], TIPOS[destino]) in PERMITIDAS[tipo], f"{tipo} de {TIPOS[origen]} a {TIPOS[destino]}"


def test_cada_componente_apunta_a_codigo_que_existe():
    for ident, tipo, nombre, _doc, codigo in modelo.ELEMENTOS:
        if tipo in ("ApplicationComponent", "ApplicationInterface", "DataObject", "Artifact"):
            assert codigo, f"{ident} ({nombre}) no dice qué archivo lo implementa"
        if codigo:
            assert (RAIZ / codigo).exists(), f"{ident}: {codigo} no existe"


def test_cada_modulo_del_nucleo_tiene_su_componente():
    """Al revés: ningún paquete de till/ falta en el modelo de aplicación."""
    codigos = {e[4] for e in modelo.ELEMENTOS if e[4]}
    paquetes = {p.parent.name for p in (RAIZ / "till").glob("*/__init__.py")}
    cubiertos = {Path(c).parts[1] for c in codigos if c.startswith("till/") and len(Path(c).parts) > 2}
    assert paquetes - cubiertos == set()
