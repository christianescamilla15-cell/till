"""Cuadre de fin de día a tres bandas.

1. Conteo físico del reciclador (casetes, rechazo, retenido, excedente)
   contra las cuentas de efectivo del libro mayor, por ubicación y denominación.
2. Libro mayor (cuentas de compensación por tipo de operación) contra los
   totales del host para la terminal.
3. Lo que explica una diferencia legítima: reversas que aún no tienen acuse,
   efectivo en tránsito por entregas parciales y efectivo de clientes retenido
   por el cajero (aclaraciones).

En producción los totales del host llegan en el archivo de liquidación del
banco o por mensajes de conciliación (0500/0510); aquí se leen del host simulado.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from till import dinero
from till.core.maquina import Cajero
from till.host.autorizador import HostAutorizador
from till.ledger import cuentas

CUADRA = "CUADRA"
PRELIMINAR = "PRELIMINAR"
DIFERENCIAS = "DIFERENCIAS"


async def cuadrar(cajero: Cajero, host: HostAutorizador, drenar_reversas: bool = True) -> dict[str, Any]:
    terminal = cajero.terminal
    libro = cajero.libro
    observaciones: list[str] = []

    if drenar_reversas:
        await cajero.reversas.drenar(terminal)

    info = await cajero.reciclador.ejecutar("CashManagement.GetCashUnitInfo")
    if not info.ok:
        return {"terminal": terminal, "estado": DIFERENCIAS, "observaciones": [f"no se pudo contar el efectivo: {info.error}"]}
    inv = info.datos
    fisico_por_ubicacion = {
        "casete": inv["casetes"],
        "rechazo": inv["rechazo"],
        "retenido": inv["retenido"],
        "excedente": inv["excedente"],
    }

    # -- 1. físico contra libro ---------------------------------------------------------
    saldos_efectivo = libro.saldos(f"cajero:{terminal}:")
    lineas_efectivo = []
    vistos: set[str] = set()
    for ubicacion, mezcla in fisico_por_ubicacion.items():
        for d in dinero.DENOMINACIONES_MXN:
            cuenta = cuentas.efectivo(terminal, ubicacion, d)
            vistos.add(cuenta)
            billetes = int(mezcla.get(d, 0))
            fisico = dinero.pesos(d) * billetes
            en_libro = saldos_efectivo.get(cuenta, 0)
            if fisico or en_libro:
                lineas_efectivo.append({
                    "cuenta": cuenta, "ubicacion": ubicacion, "denominacion": d, "billetes": billetes,
                    "fisico": fisico, "libro": en_libro, "diferencia": fisico - en_libro,
                })
    for cuenta, en_libro in saldos_efectivo.items():  # cuentas del libro sin contraparte física
        if cuenta not in vistos and en_libro:
            lineas_efectivo.append({"cuenta": cuenta, "fisico": 0, "libro": en_libro, "diferencia": -en_libro})
    total_fisico = sum(x["fisico"] for x in lineas_efectivo)
    total_libro = sum(x["libro"] for x in lineas_efectivo)
    dif_efectivo = [x for x in lineas_efectivo if x["diferencia"]]
    for x in dif_efectivo:
        tipo = "sobrante" if x["diferencia"] > 0 else "faltante"
        observaciones.append(f"{tipo} de {dinero.formatear(abs(x['diferencia']))} en {x['cuenta']}")
    interno = {"transporte": inv["transporte"], "escrow": inv["escrow"]}
    if inv["transporte"] or inv["escrow"]:
        observaciones.append("hay billetes en transporte o escrow: el cuadre se debe hacer con el cajero en reposo")

    # -- 2. libro contra host ---------------------------------------------------------------
    totales_host = host.totales_terminal(terminal)
    pendientes = cajero.bitacora.reversas_pendientes(terminal)
    en_vuelo: dict[str, int] = {}
    for r in pendientes:
        en_vuelo[r["tipo"]] = en_vuelo.get(r["tipo"], 0) + int(r["monto"])
    lineas_host = []
    explicables = True
    for tipo in cuentas.TIPOS_OPERACION:
        saldo = libro.saldo(cuentas.compensacion(terminal, tipo))
        en_libro = saldo if tipo == "retiro" else -saldo
        en_host = int(totales_host.get(tipo, 0))
        diferencia = en_host - en_libro
        maximo = en_vuelo.get(tipo, 0)
        explicable = 0 <= diferencia <= maximo
        if diferencia and not explicable:
            explicables = False
            observaciones.append(
                f"{tipo}: el banco registra {dinero.formatear(en_host)} y el libro {dinero.formatear(en_libro)}"
            )
        lineas_host.append({
            "tipo": tipo, "libro": en_libro, "host": en_host, "diferencia": diferencia,
            "reversas_en_vuelo": maximo, "explicable_por_reversas": bool(diferencia) and explicable,
        })

    transito = libro.saldo(cuentas.transito(terminal))
    aclaraciones = -libro.saldo(cuentas.aclaraciones(terminal))
    if pendientes:
        observaciones.append(f"{len(pendientes)} reversa(s) sin acuse del banco: cuadre preliminar")
    if aclaraciones:
        observaciones.append(f"{dinero.formatear(aclaraciones)} de clientes retenidos en el cajero (aclaraciones)")

    if dif_efectivo or interno["transporte"] or interno["escrow"] or not explicables:
        estado = DIFERENCIAS
    elif pendientes or transito or any(x["diferencia"] for x in lineas_host):
        estado = PRELIMINAR
    else:
        estado = CUADRA

    return {
        "terminal": terminal,
        "fecha": datetime.now(timezone.utc).isoformat(),
        "estado": estado,
        "efectivo": {
            "fisico": total_fisico,
            "libro": total_libro,
            "diferencia": total_fisico - total_libro,
            "lineas": lineas_efectivo,
            "en_proceso_interno": interno,
        },
        "host": lineas_host,
        "transito": transito,
        "reversas_pendientes": [
            {k: r[k] for k in ("tx_id", "tipo", "monto", "reemplazo", "intentos", "ultimo_error")} for r in pendientes
        ],
        "aclaraciones": {"saldo": aclaraciones, "operaciones": cajero.bitacora.aclaraciones(terminal)},
        "operaciones": cajero.bitacora.conteo_por_estado(terminal),
        "observaciones": observaciones,
    }
