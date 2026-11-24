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
