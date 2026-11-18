"""Ayudas compartidas por las pruebas de la máquina de estados."""

from __future__ import annotations

import glob

from till.devices import CorteDeEnergia
from till.recon import cuadrar
from till.red import TARJETAS_DEMO, reabrir

T = "TILL0001"
PAN = "4111111111111111"
CUENTA = "CTA-0001"


async def operar_con_reinicio(red, solicitud, pan=PAN):
    """Ejecuta la operación; si hay corte de energía, reinicia el proceso y recupera."""
    try:
        r = await red.cajero(T).operar(solicitud, pan=pan)
        return red, r, None
    except CorteDeEnergia:
        red = reabrir(red)
        resumen = await red.cajero(T).recuperar()
        return red, red.cajero(T).resultado(_ultima_tx(red)), resumen


def _ultima_tx(red):
    return red.bitacora.listar(T, limite=1)[0]["id"]


def pans_en_disco(ruta_db: str) -> list[str]:
    """Busca PANs de prueba en claro en la base y su WAL."""
    encontrados = []
    for archivo in glob.glob(ruta_db + "*"):
        with open(archivo, "rb") as f:
            contenido = f.read()
        for pan, _, _ in TARJETAS_DEMO:
            if pan.encode() in contenido:
                encontrados.append(f"{archivo}: {pan}")
    return encontrados


async def invariantes(red):
    """Lo que debe cumplirse siempre, pase lo que pase."""
    verif = red.libro.verificar()
    assert verif["balanceado"], verif
    reporte = await cuadrar(red.cajero(T), red.host)
    assert reporte["estado"] == "CUADRA", reporte["observaciones"]
    assert not pans_en_disco(red.ruta_db)
    return reporte
