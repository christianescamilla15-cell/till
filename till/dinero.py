"""Dinero y billetes.

Todos los montos internos son enteros en centavos (MXN tiene dos decimales y
el campo 4 de ISO 8583 viaja en unidades menores). Las denominaciones de los
billetes se expresan en pesos.

Una *mezcla* es un ``dict`` denominación (pesos) -> número de billetes.
"""

from __future__ import annotations

from collections.abc import Mapping

DENOMINACIONES_MXN: tuple[int, ...] = (20, 50, 100, 200, 500, 1000)

# Límite típico de billetes por entrega de un módulo reciclador (estimado).
MAX_BILLETES_POR_ENTREGA = 40

Mezcla = dict[int, int]


def pesos(p: int) -> int:
    """Convierte pesos enteros a centavos."""
    return int(p) * 100


def formatear(centavos: int) -> str:
    """Formato legible: ``$1,234.50``."""
    signo = "-" if centavos < 0 else ""
    c = abs(int(centavos))
    return f"{signo}${c // 100:,}.{c % 100:02d}"


def limpiar(mezcla: Mapping[int, int]) -> Mezcla:
    """Normaliza llaves a ``int`` y quita denominaciones en cero."""
    return {int(d): int(n) for d, n in mezcla.items() if int(n) != 0}


def valor(mezcla: Mapping[int, int]) -> int:
    """Valor de una mezcla en centavos."""
    return sum(int(d) * 100 * int(n) for d, n in mezcla.items())


def billetes(mezcla: Mapping[int, int]) -> int:
    return sum(int(n) for n in mezcla.values())


def sumar(a: Mapping[int, int], b: Mapping[int, int]) -> Mezcla:
    r: Mezcla = {int(d): int(n) for d, n in a.items()}
    for d, n in b.items():
        r[int(d)] = r.get(int(d), 0) + int(n)
    return limpiar(r)


def restar(a: Mapping[int, int], b: Mapping[int, int]) -> Mezcla:
    r: Mezcla = {int(d): int(n) for d, n in a.items()}
    for d, n in b.items():
        r[int(d)] = r.get(int(d), 0) - int(n)
        if r[int(d)] < 0:
            raise ValueError(f"mezcla negativa en {d}: {r[int(d)]}")
    return limpiar(r)


def calcular_mezcla(
    monto_centavos: int,
    disponibles: Mapping[int, int],
    max_billetes: int = MAX_BILLETES_POR_ENTREGA,
) -> Mezcla | None:
    """Encuentra una mezcla de billetes que sume exactamente ``monto_centavos``.

    Respeta los billetes disponibles por denominación y el máximo de billetes
    por entrega. Prefiere denominaciones altas (búsqueda en profundidad de
    mayor a menor con memoria de estados fallidos), así que el resultado suele
    ser el de menos billetes. El voraz simple no basta: 60 pesos con billetes de
    50 y 20 sólo se arma con tres de 20.

    Regresa ``None`` si el monto no se puede entregar.
    """
    if monto_centavos <= 0 or monto_centavos % 100:
        return None
    resto_total = monto_centavos // 100
    denoms = sorted((int(d) for d, n in disponibles.items() if int(n) > 0), reverse=True)
    fallidos: set[tuple[int, int, int]] = set()

    def buscar(i: int, resto: int, usados: int) -> Mezcla | None:
        if resto == 0:
            return {}
        if i == len(denoms):
            return None
        estado = (i, resto, usados)
        if estado in fallidos:
            return None
        d = denoms[i]
        # Cota inferior: aun usando sólo la denominación actual (la mayor que queda)
        # se necesitan ceil(resto / d) billetes.
        if usados + -(-resto // d) > max_billetes:
            fallidos.add(estado)
            return None
        tope = min(int(disponibles[d]), resto // d, max_billetes - usados)
        for n in range(tope, -1, -1):
            r = buscar(i + 1, resto - n * d, usados + n)
            if r is not None:
                if n:
                    r[d] = n
                return r
        fallidos.add(estado)
        return None

    return buscar(0, resto_total, 0)
