from till import dinero
from till.core import Solicitud
from till.recon import cuadrar
from till.red import construir_red

T = "TILL0001"


def test_cuadre_detecta_faltante_fisico(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    correr(red.cajero(T).operar(Solicitud("retiro", "a", monto=dinero.pesos(500)), pan="4111111111111111"))
    red.hardware[T].reciclador.casetes[500].cantidad -= 1  # alguien se llevó un billete
    rep = correr(cuadrar(red.cajero(T), red.host))
    assert rep["estado"] == "DIFERENCIAS"
    linea = next(x for x in rep["efectivo"]["lineas"] if x["cuenta"] == "cajero:TILL0001:casete:500")
    assert linea["diferencia"] == -dinero.pesos(500)
    assert any("faltante" in o for o in rep["observaciones"])


def test_cuadre_detecta_diferencia_con_el_host(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    correr(red.cajero(T).operar(Solicitud("retiro", "a", monto=dinero.pesos(500)), pan="4111111111111111"))
    op = next(iter(red.host.operaciones.values()))
    op.monto += dinero.pesos(100)  # el banco registra de más
    rep = correr(cuadrar(red.cajero(T), red.host))
    assert rep["estado"] == "DIFERENCIAS"
    retiro = next(x for x in rep["host"] if x["tipo"] == "retiro")
    assert retiro["diferencia"] == dinero.pesos(100) and not retiro["explicable_por_reversas"]


def test_cuadre_limpio_con_varias_operaciones(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    c = red.cajero(T)
    correr(c.operar(Solicitud("retiro", "a", monto=dinero.pesos(2_300)), pan="4111111111111111"))
    correr(c.operar(Solicitud("deposito", "b", billetes={200: 4}), pan="4012888888881881"))
    correr(c.operar(Solicitud("remesa", "c", billetes={500: 1}, destino="BENEF-1"), pan="4012888888881881"))
    rep = correr(cuadrar(c, red.host))
    assert rep["estado"] == "CUADRA"
    assert rep["efectivo"]["diferencia"] == 0
    assert {x["tipo"]: x["host"] for x in rep["host"]} == {
        "retiro": dinero.pesos(2_300), "deposito": dinero.pesos(800), "pago_credito": 0, "remesa": dinero.pesos(500)
    }


def test_cuadre_con_billetes_en_escrow_no_cuadra(tmp_path, correr):
    """El cuadre se hace con el cajero en reposo: billetes a medio depósito son una diferencia, no un saldo."""
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    r = red.hardware[T].reciclador
    correr(r.ejecutar("CashAcceptor.CashInStart", referencia="a-medias"))
    correr(r.ejecutar("CashAcceptor.CashIn", referencia="a-medias", billetes={500: 2}))
    rep = correr(cuadrar(red.cajero(T), red.host))
    assert rep["estado"] == "DIFERENCIAS" and rep["efectivo"]["en_proceso_interno"]["escrow"] == {500: 2}
    assert any("reposo" in o for o in rep["observaciones"])
