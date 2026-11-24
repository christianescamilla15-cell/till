"""Paridad contra un oráculo independiente (N08b): un día de operación contra tests/golden/lote_dia.csv.

El oráculo se escribió a mano desde las reglas (ver tests/golden/LEEME.md). Lo «real» se mide desde fuera de
la máquina de estados: el cargo y el abono salen del saldo de la cuenta en el banco simulado, y el efectivo, del
conteo físico del reciclador. Se compara por fila (llave estable + huella por fila), como multiconjunto y con
cifras control en Decimal; un control negativo prueba que la comparación sí detecta una fila alterada.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from till.core import Solicitud
from till.devices import Falla
from till.host import FallaEnlace
from till.red import construir_red

from ayudas import T, invariantes, operar_con_reinicio

GOLDEN = Path(__file__).parent / "golden"
LOTE = GOLDEN / "lote_dia.csv"
CONTROL = GOLDEN / "lote_dia.control.json"
TARJETAS = {"CTA-0001": "4111111111111111", "CTA-0002": "5555555555554444"}
COLUMNAS_COMPARADAS = ("estado", "cargo", "abono", "aclaracion")


def _efectivo(comando, falla, **kw):
    return lambda red: red.hardware[T].reciclador.fallas.programar(falla, comando, **kw)


FALLAS = {
    "ninguna": lambda red: None,
    "atasco_al_contar": _efectivo("CashDispenser.Dispense", Falla.ATASCO),
    "entrega_parcial_50": _efectivo("CashDispenser.Dispense", Falla.ENTREGA_PARCIAL, fraccion=0.5),
    "efectivo_no_retirado": _efectivo("CashDispenser.Present", Falla.NO_RETIRADO),
    "corte_tras_retiro": _efectivo("CashDispenser.Present", Falla.CORTE_ENERGIA, fase="tras_retiro"),
    "corte_antes_de_retiro": _efectivo("CashDispenser.Present", Falla.CORTE_ENERGIA, fase="antes_de_retiro"),
    "atasco_al_contar_deposito": _efectivo("CashAcceptor.CashIn", Falla.ATASCO),
    "atasco_al_guardar_y_al_devolver": lambda red: (_efectivo("CashAcceptor.CashInEnd", Falla.ATASCO)(red),
                                                    _efectivo("CashAcceptor.CashInRollback", Falla.ATASCO)(red)),
    "respuesta_0210_perdida": lambda red: red.transporte.programar(FallaEnlace.RESPUESTA_PERDIDA, "0200"),
    "host_caido": lambda red: red.transporte.programar(FallaEnlace.HOST_CAIDO, "0200"),
    "impresora_atascada": lambda red: red.hardware[T].impresora.fallas.programar(Falla.ATASCO, "Printer.PrintForm"),
}


def _pesos(centavos: int) -> str:
    return str(Decimal(centavos).scaleb(-2).quantize(Decimal("0.01")))


def _solicitud(fila: dict[str, str]) -> Solicitud:
    if fila["tipo"] == "retiro":
        return Solicitud("retiro", fila["llave"], monto=int(fila["cantidad"]) * 100)
    billetes = {int(d): int(n) for d, n in (x.split("x") for x in fila["cantidad"].split("+"))}
    return Solicitud(fila["tipo"], fila["llave"], billetes=billetes, destino=fila["destino"] or None)


def leer_oraculo() -> list[dict[str, str]]:
    with LOTE.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def huella(fila: dict[str, str]) -> str:
    return hashlib.sha256("|".join(fila[c] for c in ("llave", *COLUMNAS_COMPARADAS)).encode()).hexdigest()


def comparar(esperado: list[dict[str, str]], real: list[dict[str, str]]) -> list[str]:
    """Diferencias legibles: llaves que faltan o sobran y, por llave, columna esperada vs real."""
    diferencias = []
    por_llave = {f["llave"]: f for f in real}
    for e in esperado:
        r = por_llave.pop(e["llave"], None)
        if r is None:
            diferencias.append(f"{e['llave']}: no se ejecutó")
        elif huella(e) != huella(r):
            diferencias += [f"{e['llave']}.{c}: esperado {e[c]}, real {r[c]}" for c in COLUMNAS_COMPARADAS if e[c] != r[c]]
    diferencias += [f"{llave}: sobra" for llave in por_llave]
    return diferencias


def cifras_control(filas: list[dict[str, str]]) -> dict:
    suma = lambda c: str(sum((Decimal(f[c]) for f in filas), Decimal("0.00")))  # noqa: E731
    return {
        "operaciones": len(filas),
        "por_estado": dict(sorted(Counter(f["estado"] for f in filas).items())),
        "cargos": suma("cargo"),
        "abonos": suma("abono"),
        "aclaraciones": suma("aclaracion"),
    }


@pytest.fixture(scope="module")
def dia(tmp_path_factory):
    """Corre el lote en un cajero, operación por operación, midiendo desde el banco y desde el efectivo."""
    import asyncio

    red = construir_red(tmp_path_factory.mktemp("golden") / "till.sqlite3", cajeros=1)
    filas, salio, entro = [], 0, 0

    async def correr_lote(red):
        nonlocal salio, entro
        for fila in leer_oraculo():
            cuenta = fila["tarjeta"]
            saldo0, efectivo0 = red.host.saldo(cuenta), red.hardware[T].reciclador.valor_total()
            FALLAS[fila["falla"]](red)
            red, r, _ = await operar_con_reinicio(red, _solicitud(fila), pan=TARJETAS[cuenta])
            await red.reversas.drenar()
            for hw in red.hardware.values():
                hw.reciclador.fallas.limpiar()
                hw.impresora.fallas.limpiar()
            red.transporte.limpiar()
            delta_banco = red.host.saldo(cuenta) - saldo0
            delta_efectivo = red.hardware[T].reciclador.valor_total() - efectivo0
            salio, entro = salio + max(0, -delta_efectivo), entro + max(0, delta_efectivo)
            filas.append({
                "llave": fila["llave"],
                "estado": str(red.cajero(T).resultado(r.tx_id).estado),
                "cargo": _pesos(max(0, -delta_banco)),
                "abono": _pesos(max(0, delta_banco)),
                "aclaracion": _pesos(r.aclaracion),
            })
        await invariantes(red)
        return red

    red = asyncio.run(correr_lote(red))
    yield {"filas": filas, "salio": _pesos(salio), "entro": _pesos(entro)}
    red.cerrar()


def test_paridad_del_dia_contra_el_lote_golden(dia):
    esperado = leer_oraculo()
    assert len(esperado) >= 15
    assert comparar(esperado, dia["filas"]) == []
    proyeccion = lambda filas: Counter(tuple(f[c] for c in ("llave", *COLUMNAS_COMPARADAS)) for f in filas)  # noqa: E731
    assert proyeccion(dia["filas"]) == proyeccion(esperado)  # multiconjunto: ni de más ni de menos


def test_cifras_control_del_dia(dia):
    control = json.loads(CONTROL.read_text(encoding="utf-8"))
    base = {k: control[k] for k in ("operaciones", "por_estado", "cargos", "abonos", "aclaraciones")}
    assert cifras_control(leer_oraculo()) == base  # las cifras escritas a mano corresponden al oráculo...
    assert cifras_control(dia["filas"]) == base  # ...y a lo que pasó
    assert dia["salio"] == control["efectivo_que_salio_del_cajero"] == control["cargos"]  # nunca entrega sin cargo
    assert dia["entro"] == control["efectivo_que_entro_al_cajero"]


@pytest.mark.parametrize("llave,columna,valor", [("g03", "cargo", "1700.00"), ("g14", "aclaracion", "0.00"),
                                                 ("g11", "estado", "CONFIRMADA"), ("g07", "estado", "REVERTIDA")])
def test_control_negativo_una_fila_alterada_se_detecta(dia, llave, columna, valor):
    alterado = [dict(f, **({columna: valor} if f["llave"] == llave else {})) for f in leer_oraculo()]
    diferencias = comparar(alterado, dia["filas"])
    assert len(diferencias) == 1 and diferencias[0].startswith(f"{llave}.{columna}:")
    assert cifras_control(alterado) != cifras_control(leer_oraculo())


def test_control_negativo_filas_que_faltan_o_sobran(dia):
    assert comparar(leer_oraculo()[:-1], dia["filas"]) == ["g16: sobra"]
    assert comparar(leer_oraculo(), dia["filas"][1:]) == ["g01: no se ejecutó"]
