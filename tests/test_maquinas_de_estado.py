"""Máquinas de estado del cajero, con TODAS sus transiciones inválidas.

* La operación (bitácora): las 144 parejas de estados; lo permitido avanza y deja rastro, lo demás se
  rechaza sin tocar nada. La recuperación tras un reinicio, desde cada estado no final.
* La reversa: PENDIENTE → ACUSADA, una sola vez.
* El lector y su sesión de tarjeta, el reciclador (entrega y recepción) y el cajero en/fuera de servicio.
* Las sesiones de la API de la demo (una red por visitante).
"""

from __future__ import annotations

import re
import sqlite3
from itertools import product
from pathlib import Path

import pytest

from till import dinero
from till.api.sesiones import Sesiones
from till.core import Bitacora, CajeroFueraDeServicio, Estado, Solicitud, TransicionInvalida
from till.core.bitacora import FINALES, TRANSICIONES
from till.db import BaseDatos
from till.devices import CodigoTerminacion, CorteDeEnergia, Falla, LectorTarjetas, PanNoPermitido, RecicladorEfectivo
from till.host.iso import MotivoReversa
from till.red import construir_red, reabrir
from till.seguridad import Tokenizador

from ayudas import PAN, T

RAIZ = Path(__file__).resolve().parents[1]
DOCS_CON_DIAGRAMA_DE_ESTADOS = [RAIZ / "README.md", RAIZ / "docs" / "arquitectura" / "uml.md"]
PAREJAS = list(product(Estado, Estado))


@pytest.fixture
def bitacora(tmp_path):
    db = BaseDatos(tmp_path / "bitacora.sqlite3", sincrono="NORMAL")
    yield Bitacora(db)
    db.cerrar()


def _en_estado(bitacora: Bitacora, estado: Estado) -> str:
    """Una operación puesta directamente en ``estado`` (con los datos de una 0200 ya enviada)."""
    tx = bitacora.crear(T, "retiro", f"llave-{estado}", monto=dinero.pesos(500))
    bitacora.db.ejecutar("UPDATE transacciones SET estado = ?, stan = '000001', f7 = '1124180000', rrn = '633018000001' "
                         "WHERE id = ?", (estado.value, tx["id"]))
    return tx["id"]


# -- la operación -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("actual,nuevo", PAREJAS, ids=[f"{a.value}->{n.value}" for a, n in PAREJAS])
def test_cada_transicion_permitida_avanza_y_cada_invalida_se_rechaza_sin_cambios(bitacora, actual, nuevo):
    tx_id = _en_estado(bitacora, actual)
    pasos = len(bitacora.historial(tx_id))
    if nuevo in TRANSICIONES.get(actual, frozenset()):
        bitacora.transicion(tx_id, nuevo, detalle="prueba")
        assert bitacora.obtener(tx_id)["estado"] == nuevo
        assert bitacora.historial(tx_id)[-1] | {"momento": None} == {"de": actual, "a": nuevo, "detalle": "prueba", "momento": None}
    else:
        with pytest.raises(TransicionInvalida):
            bitacora.transicion(tx_id, nuevo, motivo="no debe quedar")
        assert bitacora.obtener(tx_id)["estado"] == actual
        assert bitacora.obtener(tx_id)["motivo"] is None  # ni los campos que venían con la transición
        assert len(bitacora.historial(tx_id)) == pasos


def test_los_estados_finales_no_tienen_salida_y_todo_lo_demas_llega_a_uno():
    assert all(not TRANSICIONES.get(f) for f in FINALES)
    for estado in Estado:
        alcanzables, pendientes = set(), [estado]
        while pendientes:
            for siguiente in TRANSICIONES.get(pendientes.pop(), frozenset()):
                if siguiente not in alcanzables:
                    alcanzables.add(siguiente)
                    pendientes.append(siguiente)
        assert estado in FINALES or alcanzables & FINALES, f"{estado} no puede terminar"
        if estado is not Estado.INICIADA:
            assert estado in _alcanzables_desde_iniciada(), f"{estado} es inalcanzable"


def _alcanzables_desde_iniciada() -> set[Estado]:
    vistos, pendientes = {Estado.INICIADA}, [Estado.INICIADA]
    while pendientes:
        for s in TRANSICIONES.get(pendientes.pop(), frozenset()):
            if s not in vistos:
                vistos.add(s)
                pendientes.append(s)
    return vistos


def test_el_diagrama_de_estados_documentado_es_el_del_codigo():
    """Los stateDiagram de la documentación dicen exactamente lo que permite TRANSICIONES."""
    esperado = {(a.value, n.value) for a, destinos in TRANSICIONES.items() for n in destinos}
    for doc in DOCS_CON_DIAGRAMA_DE_ESTADOS:
        bloque = re.search(r"```mermaid\s+stateDiagram-v2(.*?)```", doc.read_text(encoding="utf-8"), re.S).group(1)
        flechas = {(a, b) for a, b in re.findall(r"^\s*([A-Z_]+)\s*-->\s*([A-Z_]+)", bloque, re.M)}
        assert flechas == esperado, f"{doc.name}: sobran {flechas - esperado}, faltan {esperado - flechas}"


def test_transicion_de_una_operacion_inexistente(bitacora):
    with pytest.raises(TransicionInvalida):
        bitacora.transicion("no-existe", Estado.CANCELADA)


# -- la reversa ---------------------------------------------------------------------------------------------
def test_no_hay_reversa_sin_0200(bitacora):
    tx = bitacora.crear(T, "retiro", "sin-0200")
    with pytest.raises(TransicionInvalida, match="no hay 0200"):
        bitacora.encolar_reversa(tx["id"], 0, MotivoReversa.SIN_RESPUESTA)
    assert bitacora.reversa_de(tx["id"]) is None and bitacora.obtener(tx["id"])["estado"] == Estado.INICIADA


@pytest.mark.parametrize("estado", sorted(set(Estado) - {Estado.AUTORIZANDO, Estado.AUTORIZADA, Estado.ENTREGANDO, Estado.RECIBIENDO}),
                         ids=lambda e: e.value)
def test_encolar_reversa_desde_un_estado_que_no_la_admite_no_deja_nada(bitacora, estado):
    """La reversa y el cambio a EN_REVERSA son un solo paso atómico: si la transición no procede, la 0420
    tampoco queda encolada."""
    tx_id = _en_estado(bitacora, estado)
    with pytest.raises((TransicionInvalida, sqlite3.IntegrityError)):
        bitacora.encolar_reversa(tx_id, 0, MotivoReversa.FALLA_DISPOSITIVO)
    assert bitacora.reversa_de(tx_id) is None
    assert bitacora.obtener(tx_id)["estado"] == estado


def test_una_operacion_tiene_una_sola_reversa_y_se_acusa_una_vez(bitacora):
    tx_id = _en_estado(bitacora, Estado.AUTORIZADA)
    bitacora.encolar_reversa(tx_id, 0, MotivoReversa.FALLA_DISPOSITIVO)
    with pytest.raises((TransicionInvalida, sqlite3.IntegrityError)):
        bitacora.encolar_reversa(tx_id, 0, MotivoReversa.FALLA_DISPOSITIVO)
    rev = bitacora.reversa_de(tx_id)
    assert rev["estado"] == "PENDIENTE"
    assert bitacora.acusar_reversa(rev["id"]) is True
    assert bitacora.acusar_reversa(rev["id"]) is False  # ACUSADA no vuelve a acusarse
    assert bitacora.reversa_de(tx_id)["estado"] == "ACUSADA" and bitacora.reversa_de(tx_id)["intentos"] == 1


def test_una_reversa_acusada_no_se_vuelve_a_enviar(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    red.hardware[T].reciclador.fallas.programar(Falla.ATASCO, "CashDispenser.Dispense")
    r = correr(red.cajero(T).operar(Solicitud("retiro", "a", monto=dinero.pesos(500)), pan=PAN))
    assert r.estado == Estado.REVERTIDA
    enviadas = red.reversas.enviadas
    assert correr(red.reversas.intentar_tx(r.tx_id)) is False
    assert correr(red.reversas.drenar()) == {"intentadas": 0, "acusadas": 0, "pendientes": 0}
    assert red.reversas.enviadas == enviadas
    red.cerrar()


# -- recuperación tras reinicio, desde cada estado no final -----------------------------------------------
CAMINOS = {
    "retiro": [Estado.AUTORIZANDO, Estado.AUTORIZADA, Estado.ENTREGANDO],
    "deposito": [Estado.CONTANDO, Estado.AUTORIZANDO, Estado.AUTORIZADA, Estado.RECIBIENDO],
}
RECUPERACION = [
    # tipo, estado en que murió el proceso, acción de la recuperación, estado final
    ("retiro", Estado.INICIADA, "cancelada", Estado.CANCELADA),
    ("deposito", Estado.CONTANDO, "cancelada", Estado.CANCELADA),
    ("retiro", Estado.AUTORIZANDO, "reversa total", Estado.REVERTIDA),
    ("deposito", Estado.AUTORIZANDO, "reversa total", Estado.REVERTIDA),
    ("retiro", Estado.AUTORIZADA, "reversa total", Estado.REVERTIDA),
    ("retiro", Estado.ENTREGANDO, "reversa total", Estado.REVERTIDA),
    ("deposito", Estado.AUTORIZADA, "reversa total", Estado.REVERTIDA),
    ("deposito", Estado.RECIBIENDO, "reversa total", Estado.REVERTIDA),
    ("retiro", Estado.EN_REVERSA, "reversa ya encolada", Estado.REVERTIDA),
]


@pytest.mark.parametrize("tipo,estado,accion,final", RECUPERACION, ids=[f"{t}-{e.value}" for t, e, _, _ in RECUPERACION])
def test_la_recuperacion_cierra_cada_estado_pendiente(tmp_path, correr, tipo, estado, accion, final):
    """El proceso murió con la operación en ``estado`` y el dispositivo sin registro físico de ella: lo que no
    se puede probar que se entregó o se guardó, se revierte (o se cancela si nunca hubo 0200)."""
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    tx_id = red.bitacora.crear(T, tipo, "murio", monto=dinero.pesos(500) if tipo == "retiro" else 0)["id"]
    camino = [] if estado is Estado.INICIADA else CAMINOS[tipo][: CAMINOS[tipo].index(estado if estado is not Estado.EN_REVERSA else Estado.AUTORIZADA) + 1]
    for paso in camino:
        datos = {"stan": "000001", "f7": "1124180000", "rrn": "633018000001", "monto": dinero.pesos(500)} if paso is Estado.AUTORIZANDO else {}
        red.bitacora.transicion(tx_id, paso, **datos)
    if estado is Estado.EN_REVERSA:
        red.bitacora.encolar_reversa(tx_id, 0, MotivoReversa.FALLA_DISPOSITIVO)

    red = reabrir(red)
    resumen = correr(red.cajero(T).recuperar())
    assert resumen["acciones"] == [{"tx_id": tx_id, "estado_previo": estado, "accion": accion}]
    assert red.bitacora.obtener(tx_id)["estado"] == final
    assert red.bitacora.pendientes(T) == [] and red.bitacora.reversas_pendientes() == []
    assert correr(red.cajero(T).recuperar())["acciones"] == []  # recuperar otra vez no hace nada
    red.cerrar()


# -- el cajero: en servicio / fuera de servicio --------------------------------------------------------------
def test_fuera_de_servicio_rechaza_operar_hasta_recuperar(tmp_path, correr):
    red = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    red.hardware[T].reciclador.fallas.programar(Falla.CORTE_ENERGIA, "CashDispenser.Dispense")
    with pytest.raises(CorteDeEnergia):
        correr(red.cajero(T).operar(Solicitud("retiro", "a", monto=dinero.pesos(500)), pan=PAN))
    assert red.cajero(T).caido and not any(d.encendido for d in red.cajero(T).dispositivos.values())
    with pytest.raises(CajeroFueraDeServicio):
        correr(red.cajero(T).operar(Solicitud("retiro", "b", monto=dinero.pesos(500)), pan=PAN))
    correr(red.cajero(T).recuperar())
    assert not red.cajero(T).caido and all(d.encendido for d in red.cajero(T).dispositivos.values())
    assert correr(red.cajero(T).operar(Solicitud("retiro", "c", monto=dinero.pesos(500)), pan=PAN)).estado == Estado.CONFIRMADA
    red.cerrar()


def test_el_cuadre_de_un_cajero_apagado_da_503(tmp_path):
    from fastapi.testclient import TestClient

    from till.api.app import crear_app

    with TestClient(crear_app(tmp_path / "api.sqlite3", cajeros=1, reenvio_en_fondo=False)) as c:
        c.post(f"/cajeros/{T}/fallas", json={"dispositivo": "efectivo", "falla": "corte_energia", "comando": "CashDispenser.Dispense"})
        c.post(f"/cajeros/{T}/operaciones", json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500, "llave_idem": "x"})
        assert c.get(f"/cajeros/{T}/cuadre").status_code == 503
        c.post(f"/cajeros/{T}/reiniciar")
        assert c.get(f"/cajeros/{T}/cuadre").json()["estado"] == "CUADRA"


# -- el lector y la sesión de tarjeta -------------------------------------------------------------------------
def _lector(estado: str) -> LectorTarjetas:
    lector = LectorTarjetas("L1", Tokenizador())
    if estado in ("con_tarjeta", "leida", "expulsada"):
        lector.insertar(PAN)
    if estado in ("leida", "expulsada"):
        assert _correr(lector.ejecutar("CardReader.ReadRawData")).ok
    if estado == "expulsada":
        assert _correr(lector.ejecutar("CardReader.Eject")).ok
    return lector


def _correr(coro):
    import asyncio

    return asyncio.run(coro)


LECTOR_INVALIDO = [
    # estado del lector, comando que no procede ahí, error esperado
    ("sin_tarjeta", "CardReader.ReadRawData", "noMedia"),
    ("sin_tarjeta", "CardReader.GenerateAC", "noMedia"),
    ("sin_tarjeta", "CardReader.Eject", "noMedia"),
    ("con_tarjeta", "CardReader.GenerateAC", "noMedia"),  # sin leer no hay sesión ni criptograma
    ("expulsada", "CardReader.GenerateAC", "noMedia"),
    ("expulsada", "CardReader.Eject", "noMedia"),
    ("leida", "CardReader.NoExiste", "unsupportedCommand"),
]


@pytest.mark.parametrize("estado,comando,error", LECTOR_INVALIDO, ids=[f"{e}-{c.split('.')[1]}" for e, c, _ in LECTOR_INVALIDO])
def test_lector_rechaza_comandos_fuera_de_su_estado(estado, comando, error):
    t = _correr(_lector(estado).ejecutar(comando, monto=100, stan="000001", terminal=T))
    assert not t.ok and t.error == error


def test_la_sesion_de_tarjeta_vive_de_la_lectura_a_la_expulsion():
    lector = _lector("leida")
    sesion = lector.sesion
    assert sesion.activa and sesion.pan == PAN and PAN not in repr(sesion)
    assert _correr(lector.ejecutar("CardReader.GenerateAC", monto=100, stan="000001", terminal=T)).ok
    assert _correr(lector.ejecutar("CardReader.Eject")).ok
    assert not sesion.activa and lector.sesion is None and not lector.con_tarjeta
    with pytest.raises(RuntimeError):
        _ = sesion.pan


@pytest.mark.parametrize("pan", ["4111111111111112", "4242424242424242", "411111111111111", ""],
                         ids=["no_pasa_luhn", "no_es_de_la_lista", "corto", "vacio"])
def test_el_lector_solo_acepta_pans_de_prueba(pan):
    with pytest.raises(PanNoPermitido):
        LectorTarjetas("L1", Tokenizador()).insertar(pan)


def test_apagado_el_lector_no_atiende_y_al_encender_captura_la_tarjeta():
    lector = _lector("leida")
    lector._apagar()
    t = _correr(lector.ejecutar("CardReader.Eject"))
    assert t.codigo is CodigoTerminacion.NO_ENCENDIDO
    _correr(lector.encender())
    assert lector.capturadas == 1 and not lector.con_tarjeta and lector.sesion is None


# -- el reciclador: entrega y recepción --------------------------------------------------------------------------
CARGA = {20: 10, 50: 10, 100: 10, 200: 10, 500: 10, 1000: 10}


def _reciclador(estado: str) -> RecicladorEfectivo:
    r = RecicladorEfectivo("R1", CARGA, capacidad=20)
    if estado == "en_transporte":
        assert _correr(r.ejecutar("CashDispenser.Dispense", referencia="tx", mezcla={500: 1})).ok
    elif estado == "en_escrow":
        assert _correr(r.ejecutar("CashAcceptor.CashInStart", referencia="dep")).ok
        assert _correr(r.ejecutar("CashAcceptor.CashIn", referencia="dep", billetes={200: 2})).ok
    elif estado == "cash_in_iniciado":
        assert _correr(r.ejecutar("CashAcceptor.CashInStart", referencia="dep")).ok
    elif estado == "apagado":
        r._apagar()
    return r


RECICLADOR_INVALIDO = [
    # estado, comando, datos, error esperado
    ("en_reposo", "CashDispenser.Present", {"referencia": "tx"}, "noItems"),
    ("en_transporte", "CashDispenser.Present", {"referencia": "otra"}, "noItems"),
    ("en_transporte", "CashDispenser.Dispense", {"referencia": "otra", "mezcla": {100: 1}}, "transportNotEmpty"),
    ("en_reposo", "CashDispenser.Dispense", {"referencia": "tx", "mezcla": {}}, "notDispensable"),
    ("en_reposo", "CashDispenser.Dispense", {"referencia": "tx", "mezcla": {1000: 11}}, "notDispensable"),
    ("en_reposo", "CashDispenser.Denominate", {"monto": dinero.pesos(30)}, "notDispensable"),
    ("en_reposo", "CashAcceptor.CashIn", {"referencia": "dep", "billetes": {200: 1}}, "noCashInActive"),
    ("en_escrow", "CashAcceptor.CashInStart", {"referencia": "otro"}, "escrowNotEmpty"),
    ("en_escrow", "CashAcceptor.CashIn", {"referencia": "otro", "billetes": {200: 1}}, "noCashInActive"),
    ("cash_in_iniciado", "CashAcceptor.CashInEnd", {"referencia": "dep"}, "noItems"),
    ("en_escrow", "CashAcceptor.CashInEnd", {"referencia": "otro"}, "noItems"),
    ("en_escrow", "CashAcceptor.CashInRollback", {"referencia": "otro"}, "noCashInActive"),
    ("en_reposo", "Storage.Replenish", {"mezcla": {500: 11}}, "capacityExceeded"),
    ("en_reposo", "Storage.Replenish", {"mezcla": {5: 1}}, "capacityExceeded"),
    ("en_reposo", "CashDispenser.NoExiste", {}, "unsupportedCommand"),
    ("apagado", "CashManagement.GetCashUnitInfo", {}, "powerOff"),
    ("apagado", "CashDispenser.Dispense", {"referencia": "tx", "mezcla": {500: 1}}, "powerOff"),
]


@pytest.mark.parametrize("estado,comando,datos,error", RECICLADOR_INVALIDO,
                         ids=[f"{e}-{c.split('.')[1]}-{x}" for e, c, _, x in RECICLADOR_INVALIDO])
def test_reciclador_rechaza_comandos_fuera_de_su_estado_sin_mover_efectivo(estado, comando, datos, error):
    r = _reciclador(estado)
    antes = r.inventario()
    t = _correr(r.ejecutar(comando, **datos))
    assert not t.ok and t.error == error
    assert r.inventario() == antes


FASES = [
    # comandos que se ejecutan, fase final en la memoria no volátil para la referencia
    ([], "sin_operacion"),
    ([("CashDispenser.Dispense", {"mezcla": {500: 1}})], "en_transporte"),
    ([("CashDispenser.Dispense", {"mezcla": {500: 1}}), ("CashDispenser.Present", {})], "retirado"),
]


@pytest.mark.parametrize("comandos,fase", FASES, ids=[f for _, f in FASES])
def test_la_memoria_no_volatil_registra_la_fase_de_cada_entrega(comandos, fase):
    r = RecicladorEfectivo("R1", CARGA)
    for comando, datos in comandos:
        _correr(r.ejecutar(comando, referencia="tx", **datos))
    assert _correr(r.ejecutar("CashDispenser.GetPresentStatus", referencia="tx")).datos["fase"] == fase


# -- las sesiones de la API de la demo ---------------------------------------------------------------------------
def test_sesion_nueva_activa_y_purgada_por_inactividad(tmp_path):
    sesiones = Sesiones(tmp_path, cajeros=1, maximo=5, inactividad=3600)
    ent = sesiones.obtener("sesion-uno")
    ent.red.host.cuentas["CTA-0001"].saldo = 1  # la sesión tiene su propia red
    assert sesiones.obtener("sesion-uno") is ent  # activa: es la misma red
    assert sesiones.purgar() == 0
    ruta = Path(ent.red.ruta_db)
    sesiones.inactividad = -1  # todo está inactivo
    assert sesiones.purgar() == 1 and len(sesiones) == 0 and not ruta.exists()
    nueva = sesiones.obtener("sesion-uno")  # al volver: una red limpia
    assert nueva is not ent and nueva.red.host.saldo("CTA-0001") == dinero.pesos(50_000)
    sesiones.cerrar()


def test_la_sesion_usada_hace_poco_no_se_desaloja(tmp_path):
    sesiones = Sesiones(tmp_path, cajeros=1, maximo=2)
    a = sesiones.obtener("sesion-aaaa")
    sesiones.obtener("sesion-bbbb")
    sesiones.obtener("sesion-aaaa")  # «a» vuelve a ser la más reciente
    sesiones.obtener("sesion-cccc")  # se desaloja la menos reciente: «b»
    assert sesiones.obtener("sesion-aaaa") is a and len(sesiones) == 2
    sesiones.cerrar()


@pytest.mark.parametrize("sesion", ["corta", "x" * 65, "con espacio", "acento-ñññññ", "../../etc", ""])
def test_identificador_de_sesion_invalido(tmp_path, sesion):
    with pytest.raises(ValueError):
        Sesiones(tmp_path).obtener(sesion)


@pytest.mark.parametrize("columna", ["llave_idem", "huella", "terminal", "id", "estado = 'CONFIRMADA' --"])
def test_la_bitacora_solo_actualiza_columnas_editables(bitacora, columna):
    """La llave, la huella, el cajero y el id no se reescriben, y un nombre de columna nunca llega al SQL sin estar en
    la lista fija (es lo que justifica la supresión de Semgrep en el UPDATE)."""
    tx_id = _en_estado(bitacora, Estado.INICIADA)
    antes = bitacora.obtener(tx_id)
    with pytest.raises(ValueError, match="no editable"):
        bitacora.actualizar(tx_id, **{columna: "x"})
    assert bitacora.obtener(tx_id) == antes


def test_el_historial_de_transiciones_es_solo_anexar(bitacora):
    tx_id = _en_estado(bitacora, Estado.INICIADA)
    bitacora.transicion(tx_id, Estado.CANCELADA)
    for sql in ("UPDATE historial SET a = 'CONFIRMADA'", "DELETE FROM historial"):
        with pytest.raises(sqlite3.IntegrityError, match="solo anexar"):
            bitacora.db.ejecutar(sql)
    assert [h["a"] for h in bitacora.historial(tx_id)] == ["INICIADA", "CANCELADA"]


@pytest.mark.parametrize("previo,siguiente", [(None, "000001"), (1, "000002"), (999_998, "999999"), (999_999, "000001")],
                         ids=["primero", "consecutivo", "ultimo", "da_la_vuelta"])
def test_el_stan_es_consecutivo_por_cajero_y_da_la_vuelta(bitacora, previo, siguiente):
    if previo is not None:
        bitacora.db.ejecutar("INSERT INTO secuencias (terminal, stan) VALUES (?, ?)", (T, previo))
    assert bitacora.siguiente_stan(T) == siguiente
    assert bitacora.siguiente_stan("TILL0002") == "000001"  # cada cajero lleva su propia secuencia
