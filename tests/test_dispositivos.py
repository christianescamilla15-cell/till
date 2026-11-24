import pytest

from till import dinero
from till.devices import (
    Camara,
    CodigoTerminacion,
    CorteDeEnergia,
    Falla,
    FallaNoSoportada,
    ImpresoraComprobantes,
    LectorTarjetas,
    PanNoPermitido,
    RecicladorEfectivo,
)
from till.seguridad import Tokenizador

CARGA = {20: 50, 50: 50, 100: 50, 200: 50, 500: 50, 1000: 50}


def _total(r: RecicladorEfectivo) -> int:
    return r.valor_total()


def test_entrega_normal_emite_eventos(correr):
    r = RecicladorEfectivo("R1", CARGA)
    antes = _total(r)

    async def flujo():
        q = r.suscribir()
        d = await r.ejecutar("CashDispenser.Dispense", referencia="tx1", mezcla={500: 1, 200: 1})
        assert d.ok and d.datos["entregable"] == {500: 1, 200: 1}
        p = await r.ejecutar("CashDispenser.Present", referencia="tx1")
        assert p.ok and p.datos["retirado"] == {500: 1, 200: 1}
        nombres = []
        while not q.empty():
            nombres.append((await q.get()).nombre)
        return nombres

    nombres = correr(flujo())
    assert "CashDispenser.ItemsPresentedEvent" in nombres
    assert "CashManagement.ItemsTakenEvent" in nombres
    assert antes - _total(r) == dinero.pesos(700)


def test_atasco_manda_billetes_a_rechazo(correr):
    r = RecicladorEfectivo("R1", CARGA)
    r.fallas.programar(Falla.ATASCO, "CashDispenser.Dispense")
    antes = _total(r)
    t = correr(r.ejecutar("CashDispenser.Dispense", referencia="tx", mezcla={500: 2}))
    assert t.codigo is CodigoTerminacion.ERROR_HARDWARE
    assert r.rechazo == {500: 2}
    assert _total(r) == antes  # el efectivo sigue adentro, sólo cambió de lugar
    st = correr(r.ejecutar("CashDispenser.GetPresentStatus", referencia="tx"))
    assert st.datos["fase"] == "no_presentado"


def test_entrega_parcial(correr):
    r = RecicladorEfectivo("R1", CARGA)
    r.fallas.programar(Falla.ENTREGA_PARCIAL, "CashDispenser.Dispense", fraccion=0.5)
    t = correr(r.ejecutar("CashDispenser.Dispense", referencia="tx", mezcla={500: 2, 100: 2}))
    assert t.error == "partialDispense"
    assert dinero.valor(t.datos["entregable"]) + dinero.valor(t.datos["rechazo"]) == dinero.pesos(1200)
    assert t.datos["entregable"] and t.datos["rechazo"]


def test_corte_de_energia_y_arranque(correr):
    r = RecicladorEfectivo("R1", CARGA)
    r.fallas.programar(Falla.CORTE_ENERGIA, "CashDispenser.Dispense")
    with pytest.raises(CorteDeEnergia):
        correr(r.ejecutar("CashDispenser.Dispense", referencia="tx", mezcla={1000: 1}))
    assert not r.encendido and r.transporte == {1000: 1}
    t = correr(r.ejecutar("CashManagement.GetCashUnitInfo"))
    assert t.codigo is CodigoTerminacion.NO_ENCENDIDO
    correr(r.encender())
    assert r.encendido and r.rechazo == {1000: 1} and not r.transporte
    st = correr(r.ejecutar("CashDispenser.GetPresentStatus", referencia="tx"))
    assert st.datos["fase"] == "no_presentado"


def test_deposito_con_escrow_y_excedente(correr):
    r = RecicladorEfectivo("R1", {500: 0}, capacidad=3)

    async def flujo():
        await r.ejecutar("CashAcceptor.CashInStart", referencia="d1")
        c = await r.ejecutar("CashAcceptor.CashIn", referencia="d1", billetes={500: 5, 10: 1})
        assert c.datos["aceptados"] == {500: 5} and c.datos["devueltos"] == {10: 1}
        return await r.ejecutar("CashAcceptor.CashInEnd", referencia="d1")

    t = correr(flujo())
    assert t.ok and t.datos["destinos"] == {"casete": {500: 3}, "excedente": {500: 2}}


def test_devolucion_de_escrow_trabada_queda_retenida(correr):
    r = RecicladorEfectivo("R1", {})
    r.fallas.programar(Falla.ATASCO, "CashAcceptor.CashInRollback")

    async def flujo():
        await r.ejecutar("CashAcceptor.CashInStart", referencia="d1")
        await r.ejecutar("CashAcceptor.CashIn", referencia="d1", billetes={200: 3})
        return await r.ejecutar("CashAcceptor.CashInRollback", referencia="d1")

    t = correr(flujo())
    assert t.error == "retained" and r.retenido == {200: 3}


def test_falla_no_soportada():
    r = RecicladorEfectivo("R1", {})
    with pytest.raises(FallaNoSoportada):
        r.fallas.programar(Falla.ENTREGA_PARCIAL, "CashAcceptor.CashIn")
    with pytest.raises(FallaNoSoportada):
        r.fallas.programar(Falla.ATASCO, "No.Existe")


def test_lector_tokeniza_y_olvida_pan(correr):
    lector = LectorTarjetas("L1", Tokenizador())
    with pytest.raises(PanNoPermitido):
        lector.insertar("4111111111111112")
    lector.insertar("4111111111111111")
    t = correr(lector.ejecutar("CardReader.ReadRawData"))
    assert t.ok and t.datos["token"].startswith("tok_")
    assert t.datos["enmascarado"] == "411111******1111"
    assert "4111111111111111" not in repr(t)
    sesion = lector.sesion
    correr(lector.ejecutar("CardReader.Eject"))
    with pytest.raises(RuntimeError):
        _ = sesion.pan


def test_impresora_rechaza_pan_en_claro_y_camara_stub(correr):
    imp = ImpresoraComprobantes("P1")
    t = correr(imp.ejecutar("Printer.PrintForm", lineas=["TARJETA 4111 1111 1111 1111"]))
    assert t.error == "panEnClaro"
    assert correr(imp.ejecutar("Printer.PrintForm", lineas=["TARJETA 411111******1111"])).ok
    cam = Camara("C1")
    t = correr(cam.ejecutar("Camera.TakePicture", referencia="x"))
    assert t.ok and t.datos["imagen"].startswith("sim://")


def test_el_token_es_determinista_y_depende_de_la_llave():
    pan = "4111111111111111"
    a, b, otra = Tokenizador(b"llave-a"), Tokenizador(b"llave-a"), Tokenizador(b"llave-b")
    assert a.token(pan) == b.token(pan) != otra.token(pan)
    assert a.token(pan).startswith("tok_") and pan not in a.token(pan)
    assert a.token(pan) != a.token("4012888888881881")


def test_cada_criptograma_sube_el_contador_atc(correr):
    from till import seguridad

    lector = LectorTarjetas("L1", Tokenizador())
    lector.insertar("4111111111111111")
    correr(lector.ejecutar("CardReader.ReadRawData"))
    atcs = [seguridad.leer_tlv_icc(correr(lector.ejecutar("CardReader.GenerateAC", monto=100, stan="000001",
                                                                terminal="TILL0001")).datos["campo55"])[1] for _ in range(3)]
    assert atcs == [1, 2, 3]  # el mismo monto y STAN dan otro criptograma: no se puede reutilizar uno viejo
