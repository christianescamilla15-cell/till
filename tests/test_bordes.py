"""Casos límite parametrizados del cajero: montos, denominaciones, límites, reintentos, duplicados,
reversos y tiempo agotado. Cada caso nombra la regla que cuida (ver docs/reglas.md)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from till import dinero
from till.api.app import crear_app
from till.core import Estado, Solicitud, SolicitudInvalida
from till.host import iso
from till.red import construir_red

from ayudas import CUENTA, PAN, T, invariantes


@pytest.fixture
def red(tmp_path):
    r = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    yield r
    r.cerrar()


@pytest.fixture
def web(tmp_path):
    app = crear_app(tmp_path / "web.sqlite3", prefijo="/api", reenvio_en_fondo=False)
    with TestClient(app) as c:
        yield c


# -- ingresos: los billetes se validan antes de tocar el escrow ---------------------------------------
BILLETES_INVALIDOS = [
    ("cantidad_negativa", {500: -1, 200: 2}),
    ("cantidad_fraccionaria", {500: 1.5}),
    ("cantidad_booleana", {500: True}),
    ("denominacion_cero", {0: 3}),
    ("denominacion_negativa", {-500: 1}),
    ("sin_billetes", {500: 0}),
    ("total_no_cabe_en_el_campo_4", {1000: 10_000_000}),  # 10^12 centavos: 13 dígitos
]


@pytest.mark.parametrize("billetes", [b for _, b in BILLETES_INVALIDOS], ids=[n for n, _ in BILLETES_INVALIDOS])
def test_ingreso_con_billetes_invalidos_se_rechaza_sin_empezar(red, correr, billetes):
    with pytest.raises(SolicitudInvalida):
        correr(red.cajero(T).operar(Solicitud("deposito", "op-1", billetes=billetes), pan=PAN))
    assert red.bitacora.listar(T) == []  # nada empezó: ni operación ni escrow
    assert red.hardware[T].reciclador.escrow == {}


def test_el_total_maximo_del_campo_4_si_se_acepta(red, correr):
    billetes = {1000: 9_999_999}  # 999,999,900,000 centavos: cabe en 12 dígitos
    assert dinero.valor(billetes) <= iso.MONTO_MAXIMO < dinero.valor({1000: 10_000_000})
    r = correr(red.cajero(T).operar(Solicitud("deposito", "op-1", billetes=billetes), pan=PAN))
    assert r.estado == Estado.CONFIRMADA and r.monto == dinero.valor(billetes)


def test_deposito_que_no_cabe_en_el_campo_4_da_422_y_el_cajero_sigue_aceptando(web):
    s = {"X-Till-Sesion": "sesion-campo-4"}
    r = web.post(f"/api/cajeros/{T}/operaciones", headers=s | {"Idempotency-Key": "grande"},
                 json={"tipo": "deposito", "pan": PAN, "billetes": {"1000": 10_000_000}})
    assert r.status_code == 422 and "campo 4" in r.json()["detail"]
    assert web.get(f"/api/cajeros/{T}/tablero", headers=s).json()["ultima_operacion"] is None
    ok = web.post(f"/api/cajeros/{T}/operaciones", headers=s | {"Idempotency-Key": "normal"},
                  json={"tipo": "deposito", "pan": PAN, "billetes": {"500": 2}}).json()
    assert ok["estado"] == "CONFIRMADA" and ok["monto"] == dinero.pesos(1_000)


def test_sin_validacion_el_deposito_gigante_dejaba_el_escrow_ocupado(red, correr):
    """Control negativo del arreglo: si la validación se saltara, la 0200 fallaría con la operación en
    AUTORIZANDO. Se comprueba que el campo 4 de verdad no admite ese total."""
    with pytest.raises(ValueError, match="campo 4"):
        iso.monto(dinero.valor({1000: 10_000_000}))
    correr(invariantes(red))


# -- operación duplicada: misma llave, misma solicitud → la original; otra solicitud → error ---------------
def _retiro(llave="op-1", pesos=1_700, **kw):
    return Solicitud("retiro", llave, monto=dinero.pesos(pesos), **kw)


REUTILIZACIONES = [
    ("otro_monto", lambda: _retiro(pesos=2_000), PAN, T),
    ("otro_tipo", lambda: Solicitud("deposito", "op-1", billetes={500: 2}), PAN, T),
    ("otra_tarjeta", lambda: _retiro(), "4012888888881881", T),
    ("otro_cajero", lambda: _retiro(), PAN, "TILL0002"),
]


@pytest.mark.parametrize("segunda,pan,terminal", [r[1:] for r in REUTILIZACIONES], ids=[r[0] for r in REUTILIZACIONES])
def test_llave_reutilizada_con_otra_solicitud_se_rechaza(tmp_path, correr, segunda, pan, terminal):
    from till.core import LlaveReutilizada

    red = construir_red(tmp_path / "till.sqlite3", cajeros=2)
    saldo0 = red.host.saldo(CUENTA)
    primera = correr(red.cajero(T).operar(_retiro(), pan=PAN))
    with pytest.raises(LlaveReutilizada):
        correr(red.cajero(terminal).operar(segunda(), pan=pan))
    assert [t["id"] for t in red.bitacora.listar()] == [primera.tx_id]
    assert red.host.saldo(CUENTA) == saldo0 - dinero.pesos(1_700)  # un solo cargo
    red.cerrar()


def test_ingreso_repetido_con_los_mismos_billetes_es_la_misma_operacion(red, correr):
    s = Solicitud("deposito", "dep-1", billetes={500: 2, 100: 3})
    r1 = correr(red.cajero(T).operar(s, pan=PAN))
    r2 = correr(red.cajero(T).operar(Solicitud("deposito", "dep-1", billetes={100: 3, 500: 2, 200: 0}), pan=PAN))
    assert r2.repetida and r2.tx_id == r1.tx_id  # mismo contenido aunque cambie el orden o haya ceros


@pytest.mark.parametrize("veces", [2, 5, 20])
def test_solicitudes_simultaneas_con_la_misma_llave_cobran_una_vez(red, correr, veces):
    import asyncio

    saldo0 = red.host.saldo(CUENTA)

    async def todas():
        return await asyncio.gather(*(red.cajero(T).operar(_retiro(), pan=PAN) for _ in range(veces)))

    resultados = correr(todas())
    assert len({r.tx_id for r in resultados}) == 1
    assert sum(not r.repetida for r in resultados) == 1
    assert red.host.saldo(CUENTA) == saldo0 - dinero.pesos(1_700)
    assert len(red.host.operaciones) == 1  # una sola 0200 llegó al banco
    correr(invariantes(red))


def test_llave_reutilizada_por_api_da_422(web):
    s = {"X-Till-Sesion": "sesion-llave", "Idempotency-Key": "k"}
    ok = web.post(f"/api/cajeros/{T}/operaciones", headers=s, json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500})
    otra = web.post(f"/api/cajeros/{T}/operaciones", headers=s, json={"tipo": "retiro", "pan": PAN, "monto_pesos": 900})
    assert ok.status_code == 200 and otra.status_code == 422 and "ya se usó" in otra.json()["detail"]
    misma = web.post(f"/api/cajeros/{T}/operaciones", headers=s, json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500})
    assert misma.status_code == 200 and misma.json()["repetida"] and misma.json()["tx_id"] == ok.json()["tx_id"]


def test_operacion_que_espera_mientras_otra_apaga_el_cajero_no_empieza(red, correr):
    """Dos operaciones simultáneas; la primera sufre un corte. La segunda esperaba el candado: no debe
    aceptar la tarjeta ni crear una operación en un cajero apagado."""
    import asyncio

    from till.core import CajeroFueraDeServicio
    from till.devices import CorteDeEnergia, Falla

    red.hardware[T].reciclador.fallas.programar(Falla.CORTE_ENERGIA, "CashDispenser.Dispense")

    async def ambas():
        return await asyncio.gather(red.cajero(T).operar(_retiro("a"), pan=PAN),
                                    red.cajero(T).operar(_retiro("b"), pan=PAN), return_exceptions=True)

    primera, segunda = correr(ambas())
    assert isinstance(primera, CorteDeEnergia)
    assert isinstance(segunda, CajeroFueraDeServicio)
    assert [t["llave_idem"] for t in red.bitacora.listar(T)] == ["a"]
