"""Para cada falla inyectada: el libro mayor queda balanceado, el cuadre a tres
bandas cuadra y el saldo del cliente es correcto (nunca hay cargo sin entrega)."""

import pytest

from till import dinero
from till.core import Estado, Solicitud
from till.devices import Falla
from till.host import FallaEnlace
from till.red import construir_red

from ayudas import CUENTA, T, invariantes, operar_con_reinicio

RETIRO = dinero.pesos(1_700)  # 1000 + 500 + 200
DEPOSITO = {500: 2, 100: 3}  # $1,300


def _efectivo(red, comando, falla, **kw):
    red.hardware[T].reciclador.fallas.programar(falla, comando, **kw)


ESCENARIOS_RETIRO = [
    # nombre, preparación, estado final esperado, cargo esperado al cliente (pesos)
    ("sin_falla", lambda red: None, Estado.CONFIRMADA, 1_700),
    ("atasco_al_contar", lambda red: _efectivo(red, "CashDispenser.Dispense", Falla.ATASCO), Estado.REVERTIDA, 0),
    ("tiempo_agotado_al_contar", lambda red: _efectivo(red, "CashDispenser.Dispense", Falla.TIEMPO_AGOTADO), Estado.REVERTIDA, 0),
    ("entrega_parcial", lambda red: _efectivo(red, "CashDispenser.Dispense", Falla.ENTREGA_PARCIAL, fraccion=0.5), Estado.PARCIAL, 1_000),
    ("efectivo_no_retirado", lambda red: _efectivo(red, "CashDispenser.Present", Falla.NO_RETIRADO), Estado.REVERTIDA, 0),
    ("persiana_atascada", lambda red: _efectivo(red, "CashDispenser.Present", Falla.ATASCO), Estado.REVERTIDA, 0),
    ("corte_durante_conteo", lambda red: _efectivo(red, "CashDispenser.Dispense", Falla.CORTE_ENERGIA), Estado.REVERTIDA, 0),
    ("corte_tras_retiro", lambda red: _efectivo(red, "CashDispenser.Present", Falla.CORTE_ENERGIA, fase="tras_retiro"), Estado.CONFIRMADA, 1_700),
    ("corte_antes_de_retiro", lambda red: _efectivo(red, "CashDispenser.Present", Falla.CORTE_ENERGIA, fase="antes_de_retiro"), Estado.REVERTIDA, 0),
    ("respuesta_0210_perdida", lambda red: red.transporte.programar(FallaEnlace.RESPUESTA_PERDIDA, "0200"), Estado.REVERTIDA, 0),
    ("solicitud_0200_perdida", lambda red: red.transporte.programar(FallaEnlace.SOLICITUD_PERDIDA, "0200"), Estado.REVERTIDA, 0),
    ("host_caido", lambda red: red.transporte.programar(FallaEnlace.HOST_CAIDO, "0200"), Estado.REVERTIDA, 0),
    ("corte_con_0200_en_el_host", lambda red: red.transporte.programar(FallaEnlace.CORTE_TRAS_ENVIO, "0200"), Estado.REVERTIDA, 0),
    ("lector_atascado", lambda red: red.hardware[T].lector.fallas.programar(Falla.ATASCO, "CardReader.ReadRawData"), Estado.CANCELADA, 0),
    ("chip_sin_respuesta", lambda red: red.hardware[T].lector.fallas.programar(Falla.TIEMPO_AGOTADO, "CardReader.GenerateAC"), Estado.CANCELADA, 0),
    ("impresora_atascada", lambda red: red.hardware[T].impresora.fallas.programar(Falla.ATASCO, "Printer.PrintForm"), Estado.CONFIRMADA, 1_700),
]


@pytest.mark.parametrize("nombre,preparar,estado,cargo", ESCENARIOS_RETIRO, ids=[e[0] for e in ESCENARIOS_RETIRO])
def test_retiro_con_falla(tmp_path, correr, nombre, preparar, estado, cargo):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    saldo0 = red.host.saldo(CUENTA)
    efectivo0 = red.hardware[T].reciclador.valor_total()
    preparar(red)

    async def flujo():
        r_red, r, _ = await operar_con_reinicio(red, Solicitud("retiro", "op-1", monto=RETIRO))
        await r_red.reversas.drenar()
        return r_red, r_red.cajero(T).resultado(r.tx_id)

    red, r = correr(flujo())
    assert r.estado == estado, r
    assert red.host.saldo(CUENTA) == saldo0 - dinero.pesos(cargo)
    assert r.entregado == dinero.pesos(cargo)
    # El efectivo que salió del cajero es exactamente lo que se le cargó al cliente.
    assert efectivo0 - red.hardware[T].reciclador.valor_total() == dinero.pesos(cargo)
    correr(invariantes(red))


ESCENARIOS_INGRESO = [
    # nombre, tipo, destino, preparación, estado, abono esperado (pesos), retenido en aclaración (pesos)
    ("deposito_sin_falla", "deposito", None, lambda red: None, Estado.CONFIRMADA, 1_300, 0),
    ("atasco_al_contar", "deposito", None, lambda red: _efectivo(red, "CashAcceptor.CashIn", Falla.ATASCO), Estado.CANCELADA, 0, 0),
    ("tiempo_agotado_al_contar", "deposito", None, lambda red: _efectivo(red, "CashAcceptor.CashIn", Falla.TIEMPO_AGOTADO), Estado.CANCELADA, 0, 0),
    ("atasco_al_guardar_y_se_devuelve", "deposito", None, lambda red: _efectivo(red, "CashAcceptor.CashInEnd", Falla.ATASCO), Estado.REVERTIDA, 0, 0),
    ("tiempo_agotado_al_guardar", "deposito", None, lambda red: _efectivo(red, "CashAcceptor.CashInEnd", Falla.TIEMPO_AGOTADO), Estado.REVERTIDA, 0, 0),
    ("atasco_al_guardar_y_al_devolver", "deposito", None,
     lambda red: (_efectivo(red, "CashAcceptor.CashInEnd", Falla.ATASCO), _efectivo(red, "CashAcceptor.CashInRollback", Falla.ATASCO)),
     Estado.REVERTIDA, 0, 1_300),
    ("corte_al_guardar", "deposito", None, lambda red: _efectivo(red, "CashAcceptor.CashInEnd", Falla.CORTE_ENERGIA), Estado.REVERTIDA, 0, 1_300),
    ("respuesta_0210_perdida", "deposito", None, lambda red: red.transporte.programar(FallaEnlace.RESPUESTA_PERDIDA, "0200"), Estado.REVERTIDA, 0, 0),
    ("corte_con_0200_en_el_host", "deposito", None, lambda red: red.transporte.programar(FallaEnlace.CORTE_TRAS_ENVIO, "0200"), Estado.REVERTIDA, 0, 1_300),
    ("pago_credito_sin_falla", "pago_credito", "CRED-0001", lambda red: None, Estado.CONFIRMADA, 0, 0),
    ("pago_credito_atasco_al_guardar", "pago_credito", "CRED-0001", lambda red: _efectivo(red, "CashAcceptor.CashInEnd", Falla.ATASCO), Estado.REVERTIDA, 0, 0),
    ("remesa_sin_falla", "remesa", "BENEF-01", lambda red: None, Estado.CONFIRMADA, 0, 0),
    ("remesa_respuesta_perdida", "remesa", "BENEF-01", lambda red: red.transporte.programar(FallaEnlace.RESPUESTA_PERDIDA, "0200"), Estado.REVERTIDA, 0, 0),
]


@pytest.mark.parametrize("nombre,tipo,destino,preparar,estado,abono,aclaracion", ESCENARIOS_INGRESO,
                         ids=[e[0] for e in ESCENARIOS_INGRESO])
def test_ingreso_con_falla(tmp_path, correr, nombre, tipo, destino, preparar, estado, abono, aclaracion):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    saldo0 = red.host.saldo(CUENTA)
    adeudo0 = red.host.creditos["CRED-0001"]
    efectivo0 = red.hardware[T].reciclador.valor_total()
    preparar(red)

    async def flujo():
        r_red, r, _ = await operar_con_reinicio(red, Solicitud(tipo, "op-1", billetes=DEPOSITO, destino=destino))
        await r_red.reversas.drenar()
        return r_red, r_red.cajero(T).resultado(r.tx_id)

    red, r = correr(flujo())
    assert r.estado == estado, r
    assert red.host.saldo(CUENTA) == saldo0 + dinero.pesos(abono)
    assert r.aclaracion == dinero.pesos(aclaracion)
    confirmado = estado == Estado.CONFIRMADA
    if tipo == "pago_credito":
        assert red.host.creditos["CRED-0001"] == adeudo0 - (dinero.pesos(1_300) if confirmado else 0)
    if tipo == "remesa":
        vigentes = [x for x in red.host.remesas.values() if x.vigente]
        assert len(vigentes) == (1 if confirmado else 0)
    # Lo que se quedó en el cajero = lo confirmado + lo retenido por aclarar.
    dentro = red.hardware[T].reciclador.valor_total() - efectivo0
    assert dentro == (dinero.pesos(1_300) if confirmado else 0) + dinero.pesos(aclaracion)
    correr(invariantes(red))


def test_fondos_insuficientes_se_rechaza_sin_mover_efectivo(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    efectivo0 = red.hardware[T].reciclador.valor_total()
    r = correr(red.cajero(T).operar(Solicitud("retiro", "x", monto=dinero.pesos(1_700)), pan="5555555555554444"))
    assert r.estado == Estado.RECHAZADA and r.respuesta == "51"
    assert red.hardware[T].reciclador.valor_total() == efectivo0
    correr(invariantes(red))


def test_reversa_sin_acuse_se_reenvia_hasta_el_0430(tmp_path, correr):
    """Almacenar y reenviar: la 0420 se pierde varias veces, incluso tras reiniciar."""
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    saldo0 = red.host.saldo(CUENTA)
    _efectivo(red, "CashDispenser.Dispense", Falla.ATASCO)
    red.transporte.programar(FallaEnlace.RESPUESTA_PERDIDA, mti="0420")
    red.transporte.programar(FallaEnlace.RESPUESTA_PERDIDA, mti="0421", veces=3)

    async def flujo(red):
        from till.red import reabrir
        r = await red.cajero(T).operar(Solicitud("retiro", "op-1", monto=RETIRO), pan="4111111111111111")
        assert r.estado == Estado.EN_REVERSA and r.reversa_pendiente
        # Mientras no hay acuse, el banco ya devolvió el dinero (la 0420 sí llegó)
        # pero nosotros no lo damos por hecho: el cuadre es preliminar.
        from till.recon import cuadrar
        rep = await cuadrar(red.cajero(T), red.host, drenar_reversas=False)
        assert rep["estado"] == "PRELIMINAR"
        await red.reversas.drenar()  # 0421, se pierde
        red = reabrir(red)  # el proceso muere con la reversa pendiente
        await red.cajero(T).recuperar()  # 0421, se pierde
        await red.reversas.drenar()  # 0421, se pierde
        await red.reversas.drenar()  # 0421, acusada
        return red, red.cajero(T).resultado(r.tx_id)

    red, r = correr(flujo(red))
    assert r.estado == Estado.REVERTIDA and not r.reversa_pendiente
    rev = red.bitacora.reversa_de(r.tx_id)
    assert rev["estado"] == "ACUSADA" and rev["intentos"] == 5
    op = next(iter(red.host.operaciones.values()))
    assert op.avisos_reversa == 5 and op.revertido == RETIRO  # cinco avisos, un solo abono
    assert red.host.saldo(CUENTA) == saldo0
    correr(invariantes(red))


def test_misma_llave_no_repite_la_operacion(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    saldo0 = red.host.saldo(CUENTA)
    s = Solicitud("retiro", "misma-llave", monto=RETIRO)
    r1 = correr(red.cajero(T).operar(s, pan="4111111111111111"))
    r2 = correr(red.cajero(T).operar(s, pan="4111111111111111"))
    assert r1.tx_id == r2.tx_id and r2.repetida and not r1.repetida
    assert red.host.saldo(CUENTA) == saldo0 - RETIRO
    correr(invariantes(red))


def test_recuperar_dos_veces_no_duplica_nada(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    _efectivo(red, "CashDispenser.Present", Falla.CORTE_ENERGIA, fase="tras_retiro")

    async def flujo(red):
        from till.red import reabrir
        red, r, _ = await operar_con_reinicio(red, Solicitud("retiro", "op-1", monto=RETIRO))
        asientos = red.libro.verificar()["asientos"]
        red = reabrir(red)
        await red.cajero(T).recuperar()
        assert red.libro.verificar()["asientos"] == asientos
        return red, r

    red, r = correr(flujo(red))
    assert r.estado == Estado.CONFIRMADA
    correr(invariantes(red))


def test_historial_de_transiciones(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    _efectivo(red, "CashDispenser.Dispense", Falla.ENTREGA_PARCIAL)
    r = correr(red.cajero(T).operar(Solicitud("retiro", "op-1", monto=RETIRO), pan="4111111111111111"))
    pasos = [h["a"] for h in red.bitacora.historial(r.tx_id)]
    assert pasos == ["INICIADA", "AUTORIZANDO", "AUTORIZADA", "ENTREGANDO", "EN_REVERSA", "PARCIAL"]
