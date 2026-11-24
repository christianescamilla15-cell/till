#!/usr/bin/env python3
"""Requisitos de pip con las MISMAS versiones que corre el motor publicado (Pyodide).

La demo no corre la API con lo que fija requirements.txt: la corre en el navegador sobre Pyodide, con el
Python y los paquetes que trae esa distribución (p. ej. Python 3.14 y la FastAPI de su lockfile). Para que
las pruebas cubran lo que de verdad se publica, el job «motor» del CI instala estas versiones sobre el mismo
Python y corre toda la suite (lección de la semana 1: el runtime que se despliega es el que se prueba).

    npm ci --prefix web && npm run motor --prefix web     # genera web/public/motor/pyodide-<v>/pyodide-lock.json
    python3 herramientas/requisitos_motor.py > motor.txt  # paquetes del motor + herramientas de pruebas
    python3 herramientas/requisitos_motor.py --python     # sólo la versión de Python de Pyodide (p. ej. 3.14)

Las herramientas de pruebas (pytest, hypothesis, httpx...) salen de requirements-dev.txt, que es la única
fuente de sus versiones.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MOTOR = RAIZ / "web" / "public" / "motor"


def lockfile() -> dict:
    candidatos = sorted(MOTOR.glob("pyodide-*/pyodide-lock.json"))
    if len(candidatos) != 1:
        raise SystemExit(
            f"se esperaba un lockfile del motor en {MOTOR.relative_to(RAIZ)}/pyodide-*/ y hay {len(candidatos)}: "
            "corre antes `npm run motor --prefix web`"
        )
    return json.loads(candidatos[0].read_text(encoding="utf-8"))


def herramientas_de_pruebas() -> list[str]:
    """Las líneas de requirements-dev.txt que no son `-r` (lo de ejecución sale del motor, no de ahí)."""
    lineas = []
    for linea in (RAIZ / "requirements-dev.txt").read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if linea and not linea.startswith("-"):
            lineas.append(linea)
    return lineas


def main() -> int:
    lock = lockfile()
    if "--python" in sys.argv[1:]:
        print(".".join(lock["info"]["python"].split(".")[:2]))
        return 0
    paquetes = sorted(f"{p['name']}=={p['version']}" for p in lock["packages"].values())
    nombres = {re.split(r"[=<>!~ ]", x, maxsplit=1)[0].lower() for x in paquetes}
    pruebas = [x for x in herramientas_de_pruebas() if re.split(r"[=<>!~ ]", x, maxsplit=1)[0].lower() not in nombres]
    print(f"# Motor de la demo: Pyodide con Python {lock['info']['python']}", file=sys.stderr)
    print("\n".join(paquetes + pruebas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
