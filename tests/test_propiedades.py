"""Pruebas de propiedades (Hypothesis): secuencias aleatorias de operaciones con
fallas aleatorias, incluidos cortes de energía y acuses perdidos.

Después de cualquier secuencia, y una vez acusadas todas las reversas:
* el libro mayor está balanceado y el cuadre a tres bandas cuadra;
* el saldo del cliente en el banco = inicial - lo entregado + lo depositado y guardado;
* el efectivo en el cajero = inicial - lo entregado + lo guardado + lo retenido por aclarar;
* no hay PAN en claro en disco.
"""

import asyncio
import tempfile
from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st

from till import dinero
from till.core import Estado, Solicitud
from till.devices import Falla
from till.host import FallaEnlace
from till.red import construir_red

from ayudas import CUENTA, T, invariantes, operar_con_reinicio

FALLAS_RETIRO = [
    None,
    ("efectivo", "CashDispenser.Dispense", Falla.ATASCO, {}),
    ("efectivo", "CashDispenser.Dispense", Falla.TIEMPO_AGOTADO, {}),
    ("efectivo", "CashDispenser.Dispense", Falla.ENTREGA_PARCIAL, {"fraccion": 0.3}),
    ("efectivo", "CashDispenser.Dispense", Falla.ENTREGA_PARCIAL, {"fraccion": 0.8}),
    ("efectivo", "CashDispenser.Dispense", Falla.CORTE_ENERGIA, {}),
    ("efectivo", "CashDispenser.Present", Falla.NO_RETIRADO, {}),
    ("efectivo", "CashDispenser.Present", Falla.ATASCO, {}),
    ("efectivo", "CashDispenser.Present", Falla.CORTE_ENERGIA, {"fase": "tras_retiro"}),
    ("efectivo", "CashDispenser.Present", Falla.CORTE_ENERGIA, {"fase": "antes_de_retiro"}),
]
FALLAS_INGRESO = [
    None,
    ("efectivo", "CashAcceptor.CashIn", Falla.ATASCO, {}),
    ("efectivo", "CashAcceptor.CashInEnd", Falla.ATASCO, {}),
    ("efectivo", "CashAcceptor.CashInEnd", Falla.TIEMPO_AGOTADO, {}),
    ("efectivo", "CashAcceptor.CashInEnd", Falla.CORTE_ENERGIA, {}),
    ("efectivo", "CashAcceptor.CashInRollback", Falla.ATASCO, {}),
]
FALLAS_ENLACE = [
    None,
    (FallaEnlace.RESPUESTA_PERDIDA, "0200"),
    (FallaEnlace.SOLICITUD_PERDIDA, "0200"),
    (FallaEnlace.HOST_CAIDO, "0200"),
    (FallaEnlace.CORTE_TRAS_ENVIO, "0200"),
    (FallaEnlace.RESPUESTA_PERDIDA, "0420"),
    (FallaEnlace.SOLICITUD_PERDIDA, "0421"),
]

retiros = st.tuples(
    st.just("retiro"),
    st.sampled_from([100, 200, 500, 700, 1_700, 2_500, 3_000]),
    st.sampled_from(FALLAS_RETIRO),
    st.sampled_from(FALLAS_ENLACE),
)
ingresos = st.tuples(
    st.sampled_from(["deposito", "pago_credito", "remesa"]),
    st.dictionaries(st.sampled_from([20, 50, 100, 200, 500]), st.integers(1, 4), min_size=1, max_size=3),
    st.sampled_from(FALLAS_INGRESO),
    st.sampled_from(FALLAS_ENLACE),
)


async def _escenario(ruta: Path, operaciones):
    red = construir_red(ruta, cajeros=1)
    saldo0 = red.host.saldo(CUENTA)
    efectivo0 = red.hardware[T].reciclador.valor_total()
    for i, (tipo, cantidad, falla_disp, falla_enlace) in enumerate(operaciones):
        if falla_disp:
            _, comando, falla, params = falla_disp
            red.hardware[T].reciclador.fallas.programar(falla, comando, **params)
        if falla_enlace:
            red.transporte.programar(falla_enlace[0], falla_enlace[1])
        if tipo == "retiro":
            s = Solicitud("retiro", f"op-{i}", monto=dinero.pesos(cantidad))
        else:
            destino = "CRED-0001" if tipo == "pago_credito" else ("BENEF-9" if tipo == "remesa" else None)
            s = Solicitud(tipo, f"op-{i}", billetes=cantidad, destino=destino)
        red, _, _ = await operar_con_reinicio(red, s)
        # Las fallas que no alcanzaron a dispararse no deben contaminar la siguiente operación.
        red.hardware[T].reciclador.fallas.limpiar()
    red.transporte.limpiar()
    for _ in range(3):
        await red.reversas.drenar()

    txs = red.bitacora.listar(T, limite=1000)
    assert all(Estado(t["estado"]) in {Estado.CONFIRMADA, Estado.RECHAZADA, Estado.CANCELADA,
                                       Estado.REVERTIDA, Estado.PARCIAL} for t in txs)
    entregado = sum(t["entregado"] for t in txs if t["tipo"] == "retiro")
    depositado = sum(t["monto"] for t in txs if t["tipo"] == "deposito" and t["estado"] == Estado.CONFIRMADA)
    guardado = sum(t["monto"] for t in txs if t["tipo"] != "retiro" and t["estado"] == Estado.CONFIRMADA)
    retenido = sum(t["aclaracion"] for t in txs)
    assert red.host.saldo(CUENTA) == saldo0 - entregado + depositado
    assert red.hardware[T].reciclador.valor_total() == efectivo0 - entregado + guardado + retenido
    await invariantes(red)
    red.cerrar()


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.one_of(retiros, ingresos), min_size=1, max_size=6))
def test_cualquier_secuencia_con_fallas_conserva_las_invariantes(operaciones):
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(_escenario(Path(d) / "till.sqlite3", operaciones))
