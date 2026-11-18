"""Lo que la demo web usa de la API: prefijo /api, una red por sesión, tablero y traza."""

import pytest
from fastapi.testclient import TestClient

from till.api.app import crear_app, directorio_datos

T = "TILL0001"
PAN = "4111111111111111"


@pytest.fixture
def web(tmp_path):
    app = crear_app(tmp_path / "web.sqlite3", prefijo="/api", reenvio_en_fondo=False, max_sesiones=2)
    with TestClient(app) as c:
        yield c


def _s(sesion):
    return {"X-Till-Sesion": sesion}


def test_rutas_bajo_api(web):
    assert web.get("/api/salud").json()["estado"] == "ok"
    assert web.get("/salud").status_code == 404
    assert web.get("/api/openapi.json").status_code == 200


def test_sesiones_aisladas(web):
    a, b = _s("sesion-aaaa"), _s("sesion-bbbb")
    web.post(f"/api/cajeros/{T}/fallas", headers=a,
             json={"dispositivo": "efectivo", "falla": "atasco", "comando": "CashDispenser.Dispense"})
    rb = web.post(f"/api/cajeros/{T}/operaciones", headers=b | {"Idempotency-Key": "1"},
                  json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500}).json()
    ra = web.post(f"/api/cajeros/{T}/operaciones", headers=a | {"Idempotency-Key": "1"},
                  json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500}).json()
    assert rb["estado"] == "CONFIRMADA"  # la falla que armó «a» no le toca a «b»
    assert ra["estado"] == "REVERTIDA"
    assert web.get("/api/host/resumen", headers=b).json()["cuentas"]["CTA-0001"] == 4_950_000
    assert web.get("/api/host/resumen", headers=a).json()["cuentas"]["CTA-0001"] == 5_000_000
    principal = web.get("/api/host/resumen").json()["cuentas"]["CTA-0001"]
    assert principal == 5_000_000 and web.get("/api/salud", headers=a).json()["cajeros"] == 1


def test_sesion_invalida_y_tope_de_sesiones(web):
    assert web.get("/api/salud", headers=_s("x")).status_code == 422
    for s in ("sesion-1111", "sesion-2222", "sesion-3333"):  # tope 2: la menos reciente se descarta
        assert web.get("/api/salud", headers=_s(s)).status_code == 200
    assert len(web.app.state.sesiones) == 2


def test_tablero_y_detalle_con_mensajes_y_comprobante(web):
    s = _s("sesion-tablero")
    r = web.post(f"/api/cajeros/{T}/operaciones", headers=s | {"Idempotency-Key": "t"},
                 json={"tipo": "retiro", "pan": PAN, "monto_pesos": 2500}).json()
    tab = web.get(f"/api/cajeros/{T}/tablero", headers=s).json()
    assert tab["ultima_operacion"] == r["tx_id"] and tab["libro"]["verificacion"]["balanceado"]
    assert tab["banco"]["cuentas"]["CTA-0001"] == 4_750_000
    assert tab["efectivo"]["total"] == 39_450_000 and tab["fallas"]["enlace"] == []
    d = web.get(f"/api/operaciones/{r['tx_id']}", headers=s).json()
    assert [m["mti"] for m in d["mensajes"]] == ["0200"] and d["mensajes"][0]["respuesta"]["39"] == "00"
    assert "MONTO $2,500.00" in d["comprobante"]
    assert PAN not in str(d) and PAN not in str(web.get("/api/iso/mensajes", headers=s).json())


def test_reversa_con_enlace_caido_se_reintenta_respetando_la_espera(web):
    s = _s("sesion-enlace")
    assert web.post("/api/enlace/fallas", headers=s, json={"falla": "host_caido", "veces": 3}).status_code == 200
    assert len(web.get("/api/enlace/fallas", headers=s).json()["pendientes"]) == 1
    r = web.post(f"/api/cajeros/{T}/operaciones", headers=s | {"Idempotency-Key": "e"},
                 json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500}).json()
    assert r["estado"] == "EN_REVERSA" and r["reversa_pendiente"]
    # Recién falló el primer aviso: con respetar_espera todavía no toca reintentar.
    assert web.post("/api/reversas/drenar?respetar_espera=true", headers=s).json()["intentadas"] == 0
    assert web.post("/api/reversas/drenar", headers=s).json() == {"intentadas": 1, "acusadas": 0, "pendientes": 1}
    assert web.post("/api/reversas/drenar", headers=s).json() == {"intentadas": 1, "acusadas": 1, "pendientes": 0}
    d = web.get(f"/api/operaciones/{r['tx_id']}", headers=s).json()
    assert [m["mti"] for m in d["mensajes"]] == ["0200", "0420", "0421", "0421"]
    assert d["operacion"]["estado"] == "REVERTIDA" and d["reversa"]["intentos"] == 3


def test_directorio_de_datos_en_vercel(monkeypatch, tmp_path):
    monkeypatch.delenv("TILL_DATOS", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert str(directorio_datos()).endswith("/till") and "tmp" in str(directorio_datos())
    monkeypatch.setenv("TILL_DATOS", str(tmp_path))
    assert directorio_datos() == tmp_path


def test_destino_fuera_del_campo_103_se_rechaza_antes_de_operar(web):
    s = _s("sesion-destino")
    for destino in ("Ana López", "X" * 29):
        r = web.post(f"/api/cajeros/{T}/operaciones", headers=s | {"Idempotency-Key": f"r-{len(destino)}"},
                     json={"tipo": "remesa", "pan": PAN, "billetes": {"200": 3}, "destino": destino})
        assert r.status_code == 422 and "campo 103" in r.json()["detail"]
    assert web.get(f"/api/cajeros/{T}/tablero", headers=s).json()["ultima_operacion"] is None  # nada empezó
    ok = web.post(f"/api/cajeros/{T}/operaciones", headers=s | {"Idempotency-Key": "r-ok"},
                  json={"tipo": "remesa", "pan": PAN, "billetes": {"200": 3}, "destino": "Ana Lopez"}).json()
    assert ok["estado"] == "CONFIRMADA" and ok["autorizacion"]
