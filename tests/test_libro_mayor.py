import sqlite3

import pytest

from till import dinero
from till.db import BaseDatos
from till.ledger import AsientoDescuadrado, ConflictoIdempotencia, LibroMayor, cuentas


@pytest.fixture
def libro(tmp_path):
    return LibroMayor(BaseDatos(tmp_path / "libro.sqlite3"))


def test_asiento_debe_cuadrar(libro):
    with pytest.raises(AsientoDescuadrado):
        libro.registrar("x", "prueba", [("a", 100), ("b", -90)])
    with pytest.raises(AsientoDescuadrado):
        libro.registrar("y", "prueba", [("a", 100), ("a", -100)])  # se consolida a nada


def test_idempotencia_y_conflicto(libro):
    movs = cuentas.reabasto("TILL0001", {500: 10})
    a = libro.registrar("reabasto:1", "reabasto", movs)
    assert libro.registrar("reabasto:1", "reabasto", list(reversed(movs))) == a
    with pytest.raises(ConflictoIdempotencia):
        libro.registrar("reabasto:1", "reabasto", cuentas.reabasto("TILL0001", {500: 11}))
    assert libro.saldo("cajero:TILL0001:casete:500") == dinero.pesos(5_000)
    assert libro.verificar()["asientos"] == 1


def test_solo_anexar(libro):
    libro.registrar("r", "reabasto", cuentas.reabasto("TILL0001", {200: 1}))
    with pytest.raises(sqlite3.IntegrityError):
        libro.db.ejecutar("UPDATE movimientos SET monto = 1")
    with pytest.raises(sqlite3.IntegrityError):
        libro.db.ejecutar("DELETE FROM asientos")


def test_operaciones_tipo_y_verificacion(libro):
    t = "TILL0001"
    libro.registrar("reabasto", "reabasto", cuentas.reabasto(t, {500: 10, 100: 10}))
    libro.registrar("retiro", "retiro", cuentas.retiro(t, {500: 2, 100: 1}))
    libro.registrar("deposito", "deposito", cuentas.ingreso(t, "deposito", {"casete": {100: 3}, "excedente": {500: 1}}))
    libro.registrar("parcial", "retiro", cuentas.retiro_en_transito(t, {500: 1}))
    libro.registrar("parcial:ok", "retiro", cuentas.confirmar_transito(t, dinero.pesos(500)))
    libro.registrar("rechazo", "interno", cuentas.mover(t, {100: 1}, "casete", "rechazo"))
    s = libro.saldos(f"cajero:{t}:")
    assert s[f"cajero:{t}:casete:500"] == dinero.pesos(500 * 7)
    assert s[f"cajero:{t}:casete:100"] == dinero.pesos(100 * 11)
    assert s[f"cajero:{t}:excedente:500"] == dinero.pesos(500)
    assert libro.saldo(cuentas.compensacion(t, "retiro")) == dinero.pesos(1_600)
    assert libro.saldo(cuentas.compensacion(t, "deposito")) == -dinero.pesos(800)
    assert libro.saldo(cuentas.transito(t)) == 0
    v = libro.verificar()
    assert v["balanceado"] and v["suma_total"] == 0 and v["asientos"] == 6
