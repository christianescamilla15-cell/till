"""Simulador de flota: N cajeros (hasta 15 000) con tráfico sintético y fallas.

Es un modelo agregado, no 15 000 máquinas de estados: cada cajero es una fila
de arreglos (efectivo, tasa de tráfico, horas fuera de servicio...) y el
tiempo avanza por horas. Para que sea rápido en Python puro:

* el tráfico por hora sale de tablas de Poisson precalculadas por categoría de
  sucursal (10 categorías de tráfico) y hora del día: un número aleatorio y una
  búsqueda binaria por cajero y hora;
* los montos se muestrean en bloque con ``random.choices``;
* las fallas se deciden con una sola tirada por cajero y hora.

La flota se reparte en fragmentos que corren como tareas de asyncio; cada
fragmento cede el control al terminar cada hora, así la API sigue respondiendo
mientras corre una simulación. Cada fragmento tiene su propio generador con
semilla derivada: el resultado es determinista para una semilla dada.

La exactitud transaccional (reversas, cuadre, idempotencia) no se modela aquí:
la valida la muestra de alta fidelidad (``till.fleet.muestra``), que corre la
máquina de estados real. Los parámetros por omisión son supuestos razonables
para ilustrar, no datos de una red real.
"""

from __future__ import annotations

import asyncio
import bisect
import math
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

# Perfil horario de demanda en una sucursal (peso relativo por hora local).
PERFIL_HORARIO = [
    0.2, 0.1, 0.1, 0.1, 0.1, 0.3, 0.8, 1.5, 3.0, 5.5, 7.0, 7.5,
    8.0, 8.5, 8.0, 7.5, 7.5, 7.0, 6.0, 4.5, 3.0, 2.0, 1.0, 0.5,
]
_SUMA_PERFIL = sum(PERFIL_HORARIO)

# Montos de retiro (pesos) y su peso relativo.
MONTOS_RETIRO = [200, 300, 500, 1_000, 1_500, 2_000, 3_000, 5_000, 7_000]
PESOS_RETIRO = [10, 6, 20, 22, 8, 14, 10, 7, 3]
# Montos de depósito / pago / remesa en efectivo.
MONTOS_INGRESO = [500, 1_000, 2_000, 3_000, 5_000, 10_000]
PESOS_INGRESO = [15, 25, 25, 15, 12, 8]

# Fallas de dispositivo: (nombre, peso, horas fuera de servicio medias o None si no saca al cajero)
FALLAS = [
    ("atasco", 45, "mttr"),
    ("entrega_parcial", 15, "mttr"),
    ("tiempo_agotado", 20, None),
    ("corte_energia", 10, 0.25),
    ("efectivo_no_retirado", 10, None),
]

# Multiplicadores de tráfico por categoría de sucursal (10 categorías).
CATEGORIAS = [0.35, 0.5, 0.65, 0.8, 0.9, 1.0, 1.15, 1.35, 1.7, 2.3]


@dataclass
class ParametrosFlota:
    cajeros: int = 200
    horas: int = 24
    semilla: int = 2026
    tx_por_dia: float = 150.0  # operaciones por cajero por día (media de la flota)
    fraccion_ingresos: float = 0.30  # depósitos, pagos y remesas sobre el total
    prob_falla_dispositivo: float = 0.0012  # por operación (supuesto)
    prob_falla_enlace: float = 0.001  # por operación (termina en reversa, no saca al cajero)
    mttr_horas: float = 3.0  # tiempo medio de reparación con técnico
    efectivo_min: int = 250_000  # pesos, carga inicial mínima
    efectivo_max: int = 900_000  # pesos, carga inicial máxima
    capacidad: int = 1_600_000  # pesos que caben en el reciclador
    umbral_reabasto: int = 60_000  # por debajo de esto se programa traslado de valores
    umbral_servicio: int = 2_000  # por debajo de esto ya no se puede atender un retiro típico
    fecha_inicio: str = "2026-11-18"
    fragmentos: int = 32

    def validar(self) -> None:
        if not 1 <= self.cajeros <= 15_000:
            raise ValueError("cajeros debe estar entre 1 y 15 000")
        if not 1 <= self.horas <= 24 * 14:
            raise ValueError("horas debe estar entre 1 y 336")
        if self.efectivo_min > self.efectivo_max:
            raise ValueError("efectivo_min no puede ser mayor que efectivo_max")


@dataclass
class _Fragmento:
    indices: range
    rng: random.Random
    efectivo: list[int] = field(default_factory=list)
    inicial: list[int] = field(default_factory=list)
    categoria: list[int] = field(default_factory=list)
    fuera_hasta: list[float] = field(default_factory=list)
    horas_fuera: list[int] = field(default_factory=list)
    horas_sin_efectivo: list[int] = field(default_factory=list)
    retirado: list[int] = field(default_factory=list)
    ingresado: list[int] = field(default_factory=list)
    contadores: dict[str, int] = field(default_factory=dict)


def _tabla_poisson(lam: float, tope: int = 80) -> list[float]:
    """CDF acumulada de Poisson(lam) para muestrear con una búsqueda binaria."""
    cdf, p, acum = [], math.exp(-lam), 0.0
    for k in range(tope):
        acum += p
        cdf.append(acum)
        p *= lam / (k + 1)
        if acum > 1 - 1e-12:
            break
    cdf[-1] = 1.0
    return cdf


def _percentil(valores: list[float], q: float) -> float:
    if not valores:
        return 0.0
    v = sorted(valores)
    i = min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))
    return v[i]


async def simular(p: ParametrosFlota, progreso: Any = None) -> dict[str, Any]:
    p.validar()
    t0 = time.perf_counter()
    raiz = random.Random(p.semilla)

    # Tablas precalculadas: (categoría, hora) -> CDF de Poisson de retiros y de ingresos.
    tablas_ret: list[list[list[float]]] = []
    tablas_ing: list[list[list[float]]] = []
    for mult in CATEGORIAS:
        fila_r, fila_i = [], []
        for h in range(24):
            lam = p.tx_por_dia * mult * PERFIL_HORARIO[h] / _SUMA_PERFIL
            fila_r.append(_tabla_poisson(lam * (1 - p.fraccion_ingresos)))
            fila_i.append(_tabla_poisson(lam * p.fraccion_ingresos))
        tablas_ret.append(fila_r)
        tablas_ing.append(fila_i)
    p_tx = p.prob_falla_dispositivo + p.prob_falla_enlace
    prob_alguna = [1 - (1 - p_tx) ** n for n in range(200)]
    acum_ret = list(_acumular(PESOS_RETIRO))
    acum_ing = list(_acumular(PESOS_INGRESO))
    acum_fallas = list(_acumular([f[1] for f in FALLAS]))
    frac_disp = p.prob_falla_dispositivo / p_tx if p_tx else 0.0

    n_frag = max(1, min(p.fragmentos, p.cajeros))
    tam = math.ceil(p.cajeros / n_frag)
    fragmentos: list[_Fragmento] = []
    for k in range(n_frag):
        idx = range(k * tam, min(p.cajeros, (k + 1) * tam))
        if not idx:
            continue
        f = _Fragmento(idx, random.Random(raiz.getrandbits(64)))
        n = len(idx)
        f.efectivo = [f.rng.randrange(p.efectivo_min, p.efectivo_max + 1, 100) for _ in range(n)]
        f.inicial = list(f.efectivo)
        f.categoria = [f.rng.randrange(len(CATEGORIAS)) for _ in range(n)]
        f.fuera_hasta = [0.0] * n
        f.horas_fuera = [0] * n
        f.horas_sin_efectivo = [0] * n
        f.retirado = [0] * n
        f.ingresado = [0] * n
        f.contadores = {
            "retiros": 0, "ingresos": 0, "no_atendidas_fuera_de_servicio": 0, "rechazadas_sin_efectivo": 0,
            "rechazadas_capacidad": 0,
            "reversas": 0, "reversas_parciales": 0, "fallas_enlace": 0,
            **{f"falla_{nombre}": 0 for nombre, _, _ in FALLAS},
        }
        fragmentos.append(f)

    async def correr_fragmento(f: _Fragmento) -> None:
        rng = f.rng
        rnd, choices, bis = rng.random, rng.choices, bisect.bisect_left
        efectivo, categoria, fuera_hasta = f.efectivo, f.categoria, f.fuera_hasta
        horas_fuera, horas_sin, retirado, ingresado = f.horas_fuera, f.horas_sin_efectivo, f.retirado, f.ingresado
        c = f.contadores
        cap, umbral_serv, mttr = p.capacidad, p.umbral_servicio, p.mttr_horas
        for hora in range(p.horas):
            h = hora % 24
            for j in range(len(efectivo)):
                n_ret = bis(tablas_ret[categoria[j]][h], rnd())
                n_ing = bis(tablas_ing[categoria[j]][h], rnd())
                if fuera_hasta[j] > hora:
                    horas_fuera[j] += 1
                    c["no_atendidas_fuera_de_servicio"] += n_ret + n_ing
                    continue
                e = efectivo[j]
                if n_ret:
                    montos = choices(MONTOS_RETIRO, cum_weights=acum_ret, k=n_ret)
                    total = sum(montos)
                    if total <= e:
                        e -= total
                        retirado[j] += total
                        c["retiros"] += n_ret
                    else:
                        for m in montos:
                            if m <= e:
                                e -= m
                                retirado[j] += m
                                c["retiros"] += 1
                            else:
                                c["rechazadas_sin_efectivo"] += 1
                if n_ing:
                    total = sum(choices(MONTOS_INGRESO, cum_weights=acum_ing, k=n_ing))
                    if e + total <= cap:
                        e += total
                        ingresado[j] += total
                        c["ingresos"] += n_ing
                    else:  # reciclador lleno: el depósito se rechaza y el cliente conserva su efectivo
                        c["rechazadas_capacidad"] += n_ing
                efectivo[j] = e
                if e < umbral_serv:
                    horas_sin[j] += 1
                n_tx = n_ret + n_ing
                if n_tx and rnd() < prob_alguna[min(n_tx, 199)]:
                    c["reversas"] += 1
                    if rnd() < frac_disp:
                        nombre, _, fuera = FALLAS[bis(acum_fallas, rnd() * acum_fallas[-1])]
                        c[f"falla_{nombre}"] += 1
                        if nombre == "entrega_parcial":
                            c["reversas_parciales"] += 1
                        if fuera == "mttr":
                            fuera_hasta[j] = hora + 1 + rng.expovariate(1 / mttr)
                        elif fuera:
                            fuera_hasta[j] = hora + 1 + fuera
                    else:
                        c["fallas_enlace"] += 1
            await asyncio.sleep(0)  # cede el control al terminar cada hora
            if progreso:
                progreso(hora + 1, p.horas)

    await asyncio.gather(*(correr_fragmento(f) for f in fragmentos))
    duracion = time.perf_counter() - t0
    return _agregar(p, fragmentos, duracion)


def _acumular(pesos: list[int]):
    acum = 0
    for w in pesos:
        acum += w
        yield acum


def _agregar(p: ParametrosFlota, fragmentos: list[_Fragmento], duracion: float) -> dict[str, Any]:
    contadores: dict[str, int] = {}
    efectivo, disponibilidad, salida_diaria, inicial = [], [], [], []
    retirado_total = ingresado_total = 0
    urgentes: list[tuple[float, int, int]] = []
    dias = p.horas / 24
    inicio = date.fromisoformat(p.fecha_inicio)
    fin_sim = inicio + timedelta(days=dias)
    calendario: dict[str, int] = {}
    sin_necesidad = 0
    for f in fragmentos:
        for k, v in f.contadores.items():
            contadores[k] = contadores.get(k, 0) + v
        retirado_total += sum(f.retirado)
        ingresado_total += sum(f.ingresado)
        for j, idx in enumerate(f.indices):
            e = f.efectivo[j]
            efectivo.append(e)
            inicial.append(f.inicial[j])
            no_disp = min(p.horas, f.horas_fuera[j] + f.horas_sin_efectivo[j])
            disponibilidad.append(1 - no_disp / p.horas)
            neta = (f.retirado[j] - f.ingresado[j]) / dias  # salida neta de efectivo por día
            salida_diaria.append(neta)
            if neta <= 0:
                sin_necesidad += 1
                continue
            dias_restantes = max(0.0, (e - p.umbral_reabasto) / neta)
            dia = fin_sim + timedelta(days=math.floor(dias_restantes))
            clave = dia.isoformat() if dias_restantes < 7 else "despues_de_7_dias"
            calendario[clave] = calendario.get(clave, 0) + 1
            urgentes.append((dias_restantes, idx, e))
    urgentes.sort()
    n = len(efectivo)
    tx = contadores.get("retiros", 0) + contadores.get("ingresos", 0)
    cajero_horas = n * p.horas
    return {
        "parametros": asdict(p),
        "cajeros": n,
        "horas_simuladas": p.horas,
        "operaciones": {
            "atendidas": tx,
            "retiros": contadores.get("retiros", 0),
            "ingresos": contadores.get("ingresos", 0),
            "rechazadas_sin_efectivo": contadores.get("rechazadas_sin_efectivo", 0),
            "rechazadas_por_capacidad": contadores.get("rechazadas_capacidad", 0),
            "no_atendidas_fuera_de_servicio": contadores.get("no_atendidas_fuera_de_servicio", 0),
            "por_cajero_por_dia": round(tx / n / dias, 1) if n else 0,
        },
        "fallas": {k[6:]: v for k, v in contadores.items() if k.startswith("falla_")} | {
            "enlace": contadores.get("fallas_enlace", 0)
        },
        "reversas": {"total": contadores.get("reversas", 0), "parciales": contadores.get("reversas_parciales", 0)},
        "disponibilidad": {
            "flota": round(sum(disponibilidad) / n, 5) if n else 0,
            "p5": round(_percentil(disponibilidad, 0.05), 5),
            "minima": round(min(disponibilidad), 5) if n else 0,
            "cajeros_99_o_mas": sum(1 for d in disponibilidad if d >= 0.99),
        },
        "efectivo": {
            "flota_inicio": sum(inicial),
            "flota_fin": sum(efectivo),
            "retirado": retirado_total,
            "ingresado": ingresado_total,
            "por_cajero_promedio": round(sum(efectivo) / n) if n else 0,
            "por_cajero_p5": _percentil(efectivo, 0.05),
            "por_cajero_p50": _percentil(efectivo, 0.50),
            "por_cajero_p95": _percentil(efectivo, 0.95),
            "salida_neta_diaria_promedio": round(sum(salida_diaria) / n) if n else 0,
            "cajeros_bajo_umbral_reabasto": sum(1 for e in efectivo if e < p.umbral_reabasto),
        },
        "reabasto": {
            "umbral": p.umbral_reabasto,
            "cajeros_por_dia_estimado": dict(sorted(calendario.items())),
            "sin_necesidad_estimada": sin_necesidad,
            "mas_urgentes": [
                {"cajero": f"TILL{idx + 1:05d}", "efectivo": e, "dias_restantes": round(d, 2),
                 "dia_estimado": (fin_sim + timedelta(days=math.floor(d))).isoformat()}
                for d, idx, e in urgentes[:10]
            ],
        },
        "rendimiento": {
            "segundos": round(duracion, 3),
            "cajero_horas_por_segundo": round(cajero_horas / duracion) if duracion else None,
            "operaciones_por_segundo": round(tx / duracion) if duracion else None,
        },
    }
