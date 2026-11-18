"""Benchmark de la flota.

Uso:
    .venv/bin/python -m till.fleet.bench --cajeros 15000 --horas 24 --muestra 20

Corre el modelo agregado con N cajeros y, opcionalmente, una muestra de alta
fidelidad con la máquina de estados real. Imprime métricas y tiempos medidos.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path

from till import dinero
from till.fleet.muestra import correr_muestra
from till.fleet.simulador import ParametrosFlota, simular


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description="Benchmark del simulador de flota de Till")
    ap.add_argument("--cajeros", type=int, default=15_000)
    ap.add_argument("--horas", type=int, default=24)
    ap.add_argument("--semilla", type=int, default=2026)
    ap.add_argument("--muestra", type=int, default=20, help="cajeros de alta fidelidad (0 = sin muestra)")
    ap.add_argument("--operaciones", type=int, default=15, help="operaciones por cajero en la muestra")
    ap.add_argument("--json", action="store_true", help="imprimir el resultado completo en JSON")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)

    t0 = time.perf_counter()
    agregado = asyncio.run(simular(ParametrosFlota(cajeros=args.cajeros, horas=args.horas, semilla=args.semilla)))
    t_agregado = time.perf_counter() - t0

    muestra = None
    if args.muestra:
        with tempfile.TemporaryDirectory(prefix="till-bench-") as d:
            muestra = asyncio.run(correr_muestra(Path(d) / "muestra.sqlite3", args.muestra, args.operaciones,
                                                 semilla=args.semilla))
    total = time.perf_counter() - t0

    if args.json:
        print(json.dumps({"agregado": agregado, "muestra": muestra, "segundos_total": round(total, 3)},
                         ensure_ascii=False, indent=2))
    else:
        o, d, e, r = agregado["operaciones"], agregado["disponibilidad"], agregado["efectivo"], agregado["reabasto"]
        print(f"Flota agregada: {agregado['cajeros']:,} cajeros x {args.horas} h en {t_agregado:.2f} s "
              f"({agregado['rendimiento']['operaciones_por_segundo']:,} operaciones simuladas/s)")
        print(f"  operaciones atendidas {o['atendidas']:,} ({o['por_cajero_por_dia']} por cajero al día); "
              f"no atendidas por falla {o['no_atendidas_fuera_de_servicio']:,}; sin efectivo {o['rechazadas_sin_efectivo']:,}")
        print(f"  reversas {agregado['reversas']['total']:,} (parciales {agregado['reversas']['parciales']:,}); "
              f"fallas {agregado['fallas']}")
        print(f"  disponibilidad flota {d['flota']:.2%}, p5 {d['p5']:.2%}, cajeros >= 99%: {d['cajeros_99_o_mas']:,}")
        print(f"  efectivo por cajero: promedio {dinero.formatear(e['por_cajero_promedio'] * 100)}, "
              f"p5 {dinero.formatear(int(e['por_cajero_p5']) * 100)}, p95 {dinero.formatear(int(e['por_cajero_p95']) * 100)}")
        print(f"  reabasto estimado por día: {r['cajeros_por_dia_estimado']}")
        if muestra:
            print(f"Muestra de alta fidelidad: {muestra['cajeros']} cajeros, {muestra['operaciones']} operaciones, "
                  f"{muestra['reinicios_por_corte']} reinicios por corte, {muestra['segundos']:.2f} s")
            print(f"  por estado {muestra['por_estado']}; reversas {muestra['reversas']}")
            print(f"  libro balanceado: {muestra['libro_balanceado']}; cajeros que cuadran: "
                  f"{muestra['cajeros_que_cuadran']}/{muestra['cajeros']}; saldos de clientes correctos: "
                  f"{muestra['saldos_de_clientes_correctos']}")
        print(f"Tiempo total medido: {total:.2f} s")
    return {"agregado": agregado, "muestra": muestra, "segundos_total": total}


if __name__ == "__main__":
    main()
