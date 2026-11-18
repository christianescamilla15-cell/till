"""Muestra de alta fidelidad: la máquina de estados real con tráfico y fallas aleatorias.

Corre N cajeros completos (dispositivos simulados, ISO 8583 en bytes, bitácora,
libro mayor, reversas) en paralelo con asyncio, inyecta fallas al azar —incluidos
cortes de energía, tras los cuales ese cajero se reinicia y recupera— y al final
verifica lo que el modelo agregado no puede: que cada cajero cuadre a tres
bandas, que el libro esté balanceado y que el saldo de cada cliente en el banco
sea exactamente lo que físicamente pasó.
"""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path
from typing import Any

from till import dinero
from till.core import Estado, Solicitud
from till.devices import CorteDeEnergia, Falla
from till.recon import cuadrar
from till.red import TARJETAS_DEMO, construir_red, terminal_id

FALLAS_ALEATORIAS = {
    "CashDispenser.Dispense": [
        (Falla.ATASCO, {}), (Falla.ENTREGA_PARCIAL, {"fraccion": 0.5}),
        (Falla.TIEMPO_AGOTADO, {}), (Falla.CORTE_ENERGIA, {}),
    ],
    "CashDispenser.Present": [
        (Falla.NO_RETIRADO, {}), (Falla.CORTE_ENERGIA, {"fase": "tras_retiro"}),
        (Falla.CORTE_ENERGIA, {"fase": "antes_de_retiro"}),
    ],
    "CashAcceptor.CashIn": [(Falla.ATASCO, {})],
    "CashAcceptor.CashInEnd": [(Falla.ATASCO, {}), (Falla.TIEMPO_AGOTADO, {}), (Falla.CORTE_ENERGIA, {})],
    "CashAcceptor.CashInRollback": [(Falla.ATASCO, {})],
}


async def correr_muestra(
    ruta_db: str | Path,
    cajeros: int = 20,
    operaciones_por_cajero: int = 15,
    prob_falla: float = 0.08,
    prob_falla_enlace: float = 0.03,
    semilla: int = 7,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    rng = random.Random(semilla)
    red = construir_red(ruta_db, cajeros=cajeros, sincrono="NORMAL")
    for cuenta in red.host.cuentas.values():  # saldos y límites amplios: que la muestra pruebe fallas, no límites
        cuenta.saldo = dinero.pesos(10_000_000)
        cuenta.limite_retiro_diario = dinero.pesos(10_000_000)
    red.host.alta_credito("CRED-0001", dinero.pesos(10_000_000))
    saldos0 = {c: x.saldo for c, x in red.host.cuentas.items()}
    for hw in red.hardware.values():
        hw.reciclador.fallas.aleatorio(prob_falla, random.Random(rng.getrandbits(64)), FALLAS_ALEATORIAS)
    red.transporte.aleatorio(prob_falla_enlace, random.Random(rng.getrandbits(64)))
    reinicios = 0

    async def un_cajero(terminal: str, r: random.Random) -> None:
        nonlocal reinicios
        for i in range(operaciones_por_cajero):
            pan = r.choice(TARJETAS_DEMO)[0]
            tipo = r.choices(["retiro", "deposito", "pago_credito", "remesa"], [65, 22, 7, 6])[0]
            if tipo == "retiro":
                s = Solicitud("retiro", f"{terminal}-{i}", monto=dinero.pesos(r.choice([200, 500, 1_000, 1_500, 2_000, 3_000])))
            else:
                billetes = {r.choice([100, 200, 500]): r.randint(1, 5), r.choice([20, 50, 1_000]): r.randint(0, 2)}
                destino = {"pago_credito": "CRED-0001", "remesa": f"BENEF-{i}"}.get(tipo)
                s = Solicitud(tipo, f"{terminal}-{i}", billetes=billetes, destino=destino)
            try:
                await red.cajero(terminal).operar(s, pan=pan)
            except CorteDeEnergia:
                # Sólo ese cajero se reinicia: proceso nuevo sobre el mismo hardware y la misma bitácora.
                reinicios += 1
                await red._crear_cajero(terminal).recuperar()

    await asyncio.gather(*(un_cajero(terminal_id(i), random.Random(rng.getrandbits(64)))
                           for i in range(1, cajeros + 1)))
    # Fin del día: se apagan las fallas aleatorias del enlace y se drenan las reversas.
    red.transporte.limpiar()
    for hw in red.hardware.values():
        hw.reciclador.fallas.limpiar()
    for _ in range(5):
        if not (await red.reversas.drenar())["pendientes"]:
            break

    cuadres = [await cuadrar(red.cajero(t), red.host) for t in red.cajeros]
    txs = red.bitacora.listar(limite=10**7)
    por_cuenta: dict[str, int] = {c: 0 for c in saldos0}
    for t in txs:
        cuenta = red.host.tarjetas.get(t["token"] or "")
        if cuenta is None:
            continue
        if t["tipo"] == "retiro":
            por_cuenta[cuenta] -= t["entregado"]
        elif t["tipo"] == "deposito" and t["estado"] == Estado.CONFIRMADA:
            por_cuenta[cuenta] += t["monto"]
    saldos_ok = all(red.host.saldo(c) == saldos0[c] + delta for c, delta in por_cuenta.items())
    estados = red.bitacora.conteo_por_estado()
    resultado = {
        "cajeros": cajeros,
        "operaciones": len(txs),
        "por_estado": estados,
        "reinicios_por_corte": reinicios,
        "reversas": {"mensajes_enviados": red.reversas.enviadas, "acusadas": red.reversas.acusadas,
                     "pendientes": len(red.bitacora.reversas_pendientes())},
        "aclaraciones": sum(c["aclaraciones"]["saldo"] for c in cuadres),
        "libro_balanceado": red.libro.verificar()["balanceado"],
        "cajeros_que_cuadran": sum(1 for c in cuadres if c["estado"] == "CUADRA"),
        "saldos_de_clientes_correctos": saldos_ok,
        "mensajes_iso_al_host": red.host.mensajes_recibidos,
        "segundos": round(time.perf_counter() - t0, 3),
    }
    red.cerrar()
    return resultado
