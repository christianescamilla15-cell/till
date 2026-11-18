import pytest
from fastapi.testclient import TestClient

from till.api.app import crear_app

T = "TILL0001"
PAN = "4111111111111111"


@pytest.fixture
def cliente(tmp_path):
    app = crear_app(tmp_path / "api.sqlite3", cajeros=2, reenvio_en_fondo=False)
    with TestClient(app) as c:
        yield c


def test_salud_y_catalogo(cliente):
    s = cliente.get("/salud").json()
    assert s["estado"] == "ok" and s["cajeros"] == 2 and s["libro_balanceado"]
    cat = cliente.get("/catalogo").json()
    assert "CashDispenser.Dispense" in cat["fallas_por_dispositivo"]["efectivo"]
    assert cat["denominaciones"] == [20, 50, 100, 200, 500, 1000]


def test_retiro_idempotente_por_encabezado(cliente):
    cuerpo = {"tipo": "retiro", "pan": PAN, "monto_pesos": 1500}
    r1 = cliente.post(f"/cajeros/{T}/operaciones", json=cuerpo, headers={"Idempotency-Key": "abc"})
    r2 = cliente.post(f"/cajeros/{T}/operaciones", json=cuerpo, headers={"Idempotency-Key": "abc"})
    assert r1.status_code == 200 and r1.json()["estado"] == "CONFIRMADA"
    assert r1.json()["entregado_texto"] == "$1,500.00"
    assert r2.json()["tx_id"] == r1.json()["tx_id"] and r2.json()["repetida"]
    assert cliente.get("/host/resumen").json()["cuentas"]["CTA-0001"] == 4_850_000
    assert cliente.post(f"/cajeros/{T}/operaciones", json=cuerpo).status_code == 422  # sin llave


def test_pan_no_permitido_y_cajero_inexistente(cliente):
    cuerpo = {"tipo": "retiro", "pan": "4242424242424241", "monto_pesos": 100, "llave_idem": "x"}
    assert cliente.post(f"/cajeros/{T}/operaciones", json=cuerpo).status_code == 422
    assert cliente.get("/cajeros/NOEXISTE").status_code == 404


def test_falla_parcial_por_api_y_detalle_de_operacion(cliente):
    f = {"dispositivo": "efectivo", "falla": "entrega_parcial", "comando": "CashDispenser.Dispense",
         "parametros": {"fraccion": 0.5}}
    assert cliente.post(f"/cajeros/{T}/fallas", json=f).status_code == 200
    r = cliente.post(f"/cajeros/{T}/operaciones", json={"tipo": "retiro", "pan": PAN, "monto_pesos": 1700, "llave_idem": "p"}).json()
    assert r["estado"] == "PARCIAL" and r["entregado"] == 100_000
    d = cliente.get(f"/operaciones/{r['tx_id']}").json()
    assert [h["a"] for h in d["historial"]][-1] == "PARCIAL"
    assert d["reversa"]["estado"] == "ACUSADA" and d["reversa"]["reemplazo"] == 100_000
    assert {a["tipo"] for a in d["asientos"]} == {"retiro", "interno"}
    assert cliente.get(f"/cajeros/{T}/cuadre").json()["estado"] == "CUADRA"


def test_falla_invalida_da_422(cliente):
    f = {"dispositivo": "efectivo", "falla": "entrega_parcial", "comando": "CashAcceptor.CashIn"}
    assert cliente.post(f"/cajeros/{T}/fallas", json=f).status_code == 422


def test_corte_de_energia_y_reinicio(cliente):
    f = {"dispositivo": "efectivo", "falla": "corte_energia", "comando": "CashDispenser.Dispense"}
    cliente.post(f"/cajeros/{T}/fallas", json=f)
    r = cliente.post(f"/cajeros/{T}/operaciones", json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500, "llave_idem": "c"})
    assert r.status_code == 503 and r.json()["detail"]["tx_id"]
    assert T in cliente.get("/salud").json()["fuera_de_servicio"]
    otra = cliente.post(f"/cajeros/{T}/operaciones", json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500, "llave_idem": "d"})
    assert otra.status_code == 503
    rec = cliente.post(f"/cajeros/{T}/reiniciar").json()
    assert rec["acciones"][0]["accion"] == "reversa" and rec["reversas"]["pendientes"] == 0
    assert cliente.get("/salud").json()["fuera_de_servicio"] == []
    assert cliente.get(f"/cajeros/{T}/cuadre").json()["estado"] == "CUADRA"
    assert cliente.get("/host/resumen").json()["cuentas"]["CTA-0001"] == 5_000_000


def test_enlace_reversas_y_libro(cliente):
    cliente.post("/enlace/fallas", json={"falla": "respuesta_perdida", "mti": "0420", "veces": 1})
    cliente.post(f"/cajeros/{T}/fallas", json={"dispositivo": "efectivo", "falla": "atasco", "comando": "CashDispenser.Dispense"})
    r = cliente.post(f"/cajeros/{T}/operaciones", json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500, "llave_idem": "e"}).json()
    assert r["estado"] == "EN_REVERSA" and r["reversa_pendiente"]
    assert len(cliente.get("/reversas").json()) == 1
    assert cliente.get(f"/cajeros/{T}/cuadre?drenar=false").json()["estado"] == "PRELIMINAR"
    assert cliente.post("/reversas/drenar").json() == {"intentadas": 1, "acusadas": 1, "pendientes": 0}
    assert cliente.get("/libro-mayor/verificacion").json()["balanceado"]
    saldos = cliente.get(f"/libro-mayor/saldos?prefijo=cajero:{T}:rechazo").json()
    assert saldos == {f"cajero:{T}:rechazo:500": 50_000}
    assert cliente.post(f"/host/eco/{T}").json() == {"0810": True}


def test_deposito_y_flota(cliente):
    r = cliente.post(f"/cajeros/{T}/operaciones",
                     json={"tipo": "deposito", "pan": PAN, "billetes": {"500": 2}, "llave_idem": "dep"}).json()
    assert r["estado"] == "CONFIRMADA" and r["monto"] == 100_000
    assert cliente.get("/flota/metricas").status_code == 404
    m = cliente.post("/flota/simulacion", json={"cajeros": 200, "horas": 24}).json()
    assert m["cajeros"] == 200 and cliente.get("/flota/metricas").json()["cajeros"] == 200
    mu = cliente.post("/flota/muestra", json={"cajeros": 3, "operaciones_por_cajero": 5}).json()
    assert mu["libro_balanceado"] and mu["cajeros_que_cuadran"] == 3


def test_ciclo_de_fondo_reenvia_reversas_solo(tmp_path):
    import time

    app = crear_app(tmp_path / "api.sqlite3", cajeros=1, reenvio_en_fondo=True, intervalo_reenvio=0.05)
    with TestClient(app) as c:
        c.post("/enlace/fallas", json={"falla": "solicitud_perdida", "mti": "0420"})
        c.post(f"/cajeros/{T}/fallas", json={"dispositivo": "efectivo", "falla": "atasco", "comando": "CashDispenser.Dispense"})
        r = c.post(f"/cajeros/{T}/operaciones", json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500, "llave_idem": "f"}).json()
        assert r["reversa_pendiente"]
        limite = time.monotonic() + 5
        while c.get("/reversas").json() and time.monotonic() < limite:
            time.sleep(0.05)
        assert c.get("/reversas").json() == []
        assert c.get(f"/operaciones/{r['tx_id']}").json()["operacion"]["estado"] == "REVERTIDA"
