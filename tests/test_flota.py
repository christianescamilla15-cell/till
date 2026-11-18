import asyncio

import pytest

from till.fleet import ParametrosFlota, simular
from till.fleet.muestra import correr_muestra


def _sin_tiempos(r):
    return {k: v for k, v in r.items() if k != "rendimiento"}


def test_flota_200_cajeros_metricas_coherentes():
    r = asyncio.run(simular(ParametrosFlota(cajeros=200, horas=24, semilla=11)))
    assert r["cajeros"] == 200
    o, e = r["operaciones"], r["efectivo"]
    assert o["atendidas"] == o["retiros"] + o["ingresos"] > 200 * 50
    # Conservación del efectivo en el modelo agregado.
    assert e["flota_fin"] == e["flota_inicio"] - e["retirado"] + e["ingresado"]
    assert 0 < r["disponibilidad"]["flota"] <= 1
    assert r["disponibilidad"]["minima"] <= r["disponibilidad"]["p5"] <= r["disponibilidad"]["flota"] + 1e-9
    reab = r["reabasto"]
    assert sum(reab["cajeros_por_dia_estimado"].values()) + reab["sin_necesidad_estimada"] == 200
    assert len(reab["mas_urgentes"]) == 10
    assert r["reversas"]["total"] == sum(r["fallas"].values())


def test_flota_es_determinista_por_semilla():
    a = asyncio.run(simular(ParametrosFlota(cajeros=200, semilla=5)))
    b = asyncio.run(simular(ParametrosFlota(cajeros=200, semilla=5)))
    c = asyncio.run(simular(ParametrosFlota(cajeros=200, semilla=6)))
    assert _sin_tiempos(a) == _sin_tiempos(b)
    assert _sin_tiempos(a) != _sin_tiempos(c)


def test_flota_valida_parametros():
    with pytest.raises(ValueError):
        asyncio.run(simular(ParametrosFlota(cajeros=15_001)))


def test_muestra_de_alta_fidelidad_cuadra(tmp_path):
    r = asyncio.run(correr_muestra(tmp_path / "muestra.sqlite3", cajeros=10, operaciones_por_cajero=12,
                                   prob_falla=0.15, prob_falla_enlace=0.05, semilla=3))
    assert r["operaciones"] == 120
    assert r["libro_balanceado"]
    assert r["cajeros_que_cuadran"] == 10
    assert r["saldos_de_clientes_correctos"]
    assert r["reversas"]["pendientes"] == 0
    assert r["reinicios_por_corte"] > 0  # la muestra sí ejercita cortes de energía
