"""Catálogo de cuentas y asientos tipo de Till.

Convención: débito positivo, crédito negativo, en centavos.

Cuentas de efectivo físico del cajero (activo), una por ubicación y denominación:

    cajero:<terminal>:casete:<denominación>
    cajero:<terminal>:rechazo:<denominación>
    cajero:<terminal>:retenido:<denominación>
    cajero:<terminal>:excedente:<denominación>

Cuentas de compensación con el banco, una por terminal y tipo de operación:

    compensacion:<terminal>:retiro        (saldo deudor: el banco nos debe lo entregado)
    compensacion:<terminal>:deposito      (saldo acreedor: le debemos al banco lo recibido)
    compensacion:<terminal>:pago_credito
    compensacion:<terminal>:remesa

Cuentas puente:

    transito:<terminal>      efectivo ya entregado cuya reversa parcial no ha sido acusada
    aclaraciones:<terminal>  efectivo de clientes retenido por el cajero, pendiente de devolver
    boveda:central           origen/destino del efectivo que llevan los traslados de valores
"""

from __future__ import annotations

from till import dinero
from till.dinero import Mezcla
from till.ledger.libro import Movimiento

UBICACIONES = ("casete", "rechazo", "retenido", "excedente")
TIPOS_OPERACION = ("retiro", "deposito", "pago_credito", "remesa")
BOVEDA = "boveda:central"


def efectivo(terminal: str, ubicacion: str, denominacion: int) -> str:
    if ubicacion not in UBICACIONES:
        raise ValueError(f"ubicación desconocida: {ubicacion}")
    return f"cajero:{terminal}:{ubicacion}:{int(denominacion)}"


def compensacion(terminal: str, tipo: str) -> str:
    if tipo not in TIPOS_OPERACION:
        raise ValueError(f"tipo de operación desconocido: {tipo}")
    return f"compensacion:{terminal}:{tipo}"


def transito(terminal: str) -> str:
    return f"transito:{terminal}"


def aclaraciones(terminal: str) -> str:
    return f"aclaraciones:{terminal}"


# -- piezas ----------------------------------------------------------------------
def entra(terminal: str, ubicacion: str, mezcla: Mezcla) -> list[Movimiento]:
    return [(efectivo(terminal, ubicacion, d), dinero.pesos(d) * n) for d, n in mezcla.items() if n]


def sale(terminal: str, ubicacion: str, mezcla: Mezcla) -> list[Movimiento]:
    return [(efectivo(terminal, ubicacion, d), -dinero.pesos(d) * n) for d, n in mezcla.items() if n]


# -- asientos tipo -------------------------------------------------------------------
def reabasto(terminal: str, mezcla: Mezcla) -> list[Movimiento]:
    return entra(terminal, "casete", mezcla) + [(BOVEDA, -dinero.valor(mezcla))]


def retiro(terminal: str, entregado: Mezcla) -> list[Movimiento]:
    """Retiro confirmado por el dispositivo y aprobado por el banco."""
    return [(compensacion(terminal, "retiro"), dinero.valor(entregado))] + sale(terminal, "casete", entregado)


def retiro_en_transito(terminal: str, entregado: Mezcla) -> list[Movimiento]:
    """Entrega parcial: el efectivo salió pero falta el acuse de la reversa parcial."""
    return [(transito(terminal), dinero.valor(entregado))] + sale(terminal, "casete", entregado)


def confirmar_transito(terminal: str, monto: int) -> list[Movimiento]:
    return [(compensacion(terminal, "retiro"), monto), (transito(terminal), -monto)]


def ingreso(terminal: str, tipo: str, destinos: dict[str, Mezcla]) -> list[Movimiento]:
    """Depósito, pago de crédito o remesa: el efectivo entra a casetes (o excedente)."""
    movs: list[Movimiento] = []
    total = 0
    for ubicacion in ("casete", "excedente"):
        m = destinos.get(ubicacion, {})
        movs += entra(terminal, ubicacion, m)
        total += dinero.valor(m)
    return movs + [(compensacion(terminal, tipo), -total)]


def mover(terminal: str, mezcla: Mezcla, origen: str, destino: str) -> list[Movimiento]:
    """Movimiento interno entre ubicaciones del mismo cajero (p. ej. casete -> rechazo)."""
    return entra(terminal, destino, mezcla) + sale(terminal, origen, mezcla)


def retenido_por_aclarar(terminal: str, mezcla: Mezcla) -> list[Movimiento]:
    """Efectivo de un cliente que el cajero se quedó y hay que devolverle."""
    return entra(terminal, "retenido", mezcla) + [(aclaraciones(terminal), -dinero.valor(mezcla))]
