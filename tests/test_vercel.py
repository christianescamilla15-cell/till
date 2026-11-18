"""La entrada de Vercel (api/index.py): rutas bajo /api, encabezados de seguridad y vercel.json."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture
def entrada(tmp_path, monkeypatch):
    monkeypatch.setenv("TILL_DATOS", str(tmp_path))  # nada de escribir en datos/ del repo
    from api import index

    with TestClient(index.app) as c:
        yield index, c


def test_api_bajo_prefijo_con_encabezados(entrada):
    index, c = entrada
    r = c.get("/api/salud", headers={"X-Till-Sesion": "sesion-vercel"})
    assert r.status_code == 200 and r.json()["cajeros"] == 1
    assert r.headers["content-security-policy"] == index.CSP
    assert "unsafe" not in index.CSP and "default-src 'self'" in index.CSP
    assert c.get("/api/docs").status_code == 404  # Swagger usa una CDN: fuera de la demo
    assert c.get("/api/openapi.json").status_code == 200


def test_bases_en_el_directorio_de_datos(entrada, tmp_path):
    _, c = entrada
    c.get("/api/salud", headers={"X-Till-Sesion": "sesion-datos"})
    assert (tmp_path / "till-demo.sqlite3").exists()
    assert (tmp_path / "sesiones" / "sesion-sesion-datos.sqlite3").exists()


def test_vercel_json_coincide_con_la_entrada(entrada):
    index, _ = entrada
    cfg = json.loads((RAIZ / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["outputDirectory"] == "web/dist" and "api/index.py" in cfg["functions"]
    assert {"source": "/api/(.*)", "destination": "/api/index"} in cfg["rewrites"]
    globales = next(h["headers"] for h in cfg["headers"] if h["source"] == "/(.*)")
    assert {h["key"]: h["value"] for h in globales} == index.ENCABEZADOS


def test_sirve_el_front_construido_en_local(entrada):
    index, c = entrada
    if not index.DIST.is_dir():
        pytest.skip("web/dist no está construido (npm run build --prefix web)")
    r = c.get("/")
    assert r.status_code == 200 and '<div id="raiz">' in r.text
    assert r.headers["content-security-policy"] == index.CSP
