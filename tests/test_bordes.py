"""Casos límite parametrizados del cajero: montos, denominaciones, límites, reintentos, duplicados,
reversos y tiempo agotado. Cada caso nombra la regla que cuida (ver docs/reglas.md)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from till import dinero
from till.api.app import crear_app
from till.core import Estado, Solicitud, SolicitudInvalida
from till.host import iso
from till.red import construir_red

from ayudas import CUENTA, PAN, T, invariantes


@pytest.fixture
def red(tmp_path):
    r = construir_red(tmp_path / "till.sqlite3", cajeros=1)
    yield r
    r.cerrar()


@pytest.fixture
def web(tmp_path):
    app = crear_app(tmp_path / "web.sqlite3", prefijo="/api", reenvio_en_fondo=False)
    with TestClient(app) as c:
        yield c


# -- ingresos: los billetes se validan antes de tocar el escrow ---------------------------------------
BILLETES_INVALIDOS = [
    ("cantidad_negativa", {500: -1, 200: 2}),
    ("cantidad_fraccionaria", {500: 1.5}),
    ("cantidad_booleana", {500: True}),
    ("denominacion_cero", {0: 3}),
    ("denominacion_negativa", {-500: 1}),
    ("sin_billetes", {500: 0}),
    ("total_no_cabe_en_el_campo_4", {1000: 10_000_000}),  # 10^12 centavos: 13 dígitos
]


@pytest.mark.parametrize("billetes", [b for _, b in BILLETES_INVALIDOS], ids=[n for n, _ in BILLETES_INVALIDOS])
def test_ingreso_con_billetes_invalidos_se_rechaza_sin_empezar(red, correr, billetes):
    with pytest.raises(SolicitudInvalida):
        correr(red.cajero(T).operar(Solicitud("deposito", "op-1", billetes=billetes), pan=PAN))
    assert red.bitacora.listar(T) == []  # nada empezó: ni operación ni escrow
    assert red.hardware[T].reciclador.escrow == {}


def test_el_total_maximo_del_campo_4_si_se_acepta(red, correr):
    billetes = {1000: 9_999_999}  # 999,999,900,000 centavos: cabe en 12 dígitos
    assert dinero.valor(billetes) <= iso.MONTO_MAXIMO < dinero.valor({1000: 10_000_000})
    r = correr(red.cajero(T).operar(Solicitud("deposito", "op-1", billetes=billetes), pan=PAN))
    assert r.estado == Estado.CONFIRMADA and r.monto == dinero.valor(billetes)


def test_deposito_que_no_cabe_en_el_campo_4_da_422_y_el_cajero_sigue_aceptando(web):
    s = {"X-Till-Sesion": "sesion-campo-4"}
    r = web.post(f"/api/cajeros/{T}/operaciones", headers=s | {"Idempotency-Key": "grande"},
                 json={"tipo": "deposito", "pan": PAN, "billetes": {"1000": 10_000_000}})
    assert r.status_code == 422 and "campo 4" in r.json()["detail"]
    assert web.get(f"/api/cajeros/{T}/tablero", headers=s).json()["ultima_operacion"] is None
    ok = web.post(f"/api/cajeros/{T}/operaciones", headers=s | {"Idempotency-Key": "normal"},
                  json={"tipo": "deposito", "pan": PAN, "billetes": {"500": 2}}).json()
    assert ok["estado"] == "CONFIRMADA" and ok["monto"] == dinero.pesos(1_000)


def test_sin_validacion_el_deposito_gigante_dejaba_el_escrow_ocupado(red, correr):
    """Control negativo del arreglo: si la validación se saltara, la 0200 fallaría con la operación en
    AUTORIZANDO. Se comprueba que el campo 4 de verdad no admite ese total."""
    with pytest.raises(ValueError, match="campo 4"):
        iso.monto(dinero.valor({1000: 10_000_000}))
    correr(invariantes(red))


# -- operación duplicada: misma llave, misma solicitud → la original; otra solicitud → error ---------------
def _retiro(llave="op-1", pesos=1_700, **kw):
    return Solicitud("retiro", llave, monto=dinero.pesos(pesos), **kw)


REUTILIZACIONES = [
    ("otro_monto", lambda: _retiro(pesos=2_000), PAN, T),
    ("otro_tipo", lambda: Solicitud("deposito", "op-1", billetes={500: 2}), PAN, T),
    ("otra_tarjeta", lambda: _retiro(), "4012888888881881", T),
    ("otro_cajero", lambda: _retiro(), PAN, "TILL0002"),
]


@pytest.mark.parametrize("segunda,pan,terminal", [r[1:] for r in REUTILIZACIONES], ids=[r[0] for r in REUTILIZACIONES])
def test_llave_reutilizada_con_otra_solicitud_se_rechaza(tmp_path, correr, segunda, pan, terminal):
    from till.core import LlaveReutilizada

    red = construir_red(tmp_path / "till.sqlite3", cajeros=2)
    saldo0 = red.host.saldo(CUENTA)
    primera = correr(red.cajero(T).operar(_retiro(), pan=PAN))
    with pytest.raises(LlaveReutilizada):
        correr(red.cajero(terminal).operar(segunda(), pan=pan))
    assert [t["id"] for t in red.bitacora.listar()] == [primera.tx_id]
    assert red.host.saldo(CUENTA) == saldo0 - dinero.pesos(1_700)  # un solo cargo
    red.cerrar()


def test_ingreso_repetido_con_los_mismos_billetes_es_la_misma_operacion(red, correr):
    s = Solicitud("deposito", "dep-1", billetes={500: 2, 100: 3})
    r1 = correr(red.cajero(T).operar(s, pan=PAN))
    r2 = correr(red.cajero(T).operar(Solicitud("deposito", "dep-1", billetes={100: 3, 500: 2, 200: 0}), pan=PAN))
    assert r2.repetida and r2.tx_id == r1.tx_id  # mismo contenido aunque cambie el orden o haya ceros


@pytest.mark.parametrize("veces", [2, 5, 20])
def test_solicitudes_simultaneas_con_la_misma_llave_cobran_una_vez(red, correr, veces):
    import asyncio

    saldo0 = red.host.saldo(CUENTA)

    async def todas():
        return await asyncio.gather(*(red.cajero(T).operar(_retiro(), pan=PAN) for _ in range(veces)))

    resultados = correr(todas())
    assert len({r.tx_id for r in resultados}) == 1
    assert sum(not r.repetida for r in resultados) == 1
    assert red.host.saldo(CUENTA) == saldo0 - dinero.pesos(1_700)
    assert len(red.host.operaciones) == 1  # una sola 0200 llegó al banco
    correr(invariantes(red))


def test_llave_reutilizada_por_api_da_422(web):
    s = {"X-Till-Sesion": "sesion-llave", "Idempotency-Key": "k"}
    ok = web.post(f"/api/cajeros/{T}/operaciones", headers=s, json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500})
    otra = web.post(f"/api/cajeros/{T}/operaciones", headers=s, json={"tipo": "retiro", "pan": PAN, "monto_pesos": 900})
    assert ok.status_code == 200 and otra.status_code == 422 and "ya se usó" in otra.json()["detail"]
    misma = web.post(f"/api/cajeros/{T}/operaciones", headers=s, json={"tipo": "retiro", "pan": PAN, "monto_pesos": 500})
    assert misma.status_code == 200 and misma.json()["repetida"] and misma.json()["tx_id"] == ok.json()["tx_id"]


def test_operacion_que_espera_mientras_otra_apaga_el_cajero_no_empieza(red, correr):
    """Dos operaciones simultáneas; la primera sufre un corte. La segunda esperaba el candado: no debe
    aceptar la tarjeta ni crear una operación en un cajero apagado."""
    import asyncio

    from till.core import CajeroFueraDeServicio
    from till.devices import CorteDeEnergia, Falla

    red.hardware[T].reciclador.fallas.programar(Falla.CORTE_ENERGIA, "CashDispenser.Dispense")

    async def ambas():
        return await asyncio.gather(red.cajero(T).operar(_retiro("a"), pan=PAN),
                                    red.cajero(T).operar(_retiro("b"), pan=PAN), return_exceptions=True)

    primera, segunda = correr(ambas())
    assert isinstance(primera, CorteDeEnergia)
    assert isinstance(segunda, CajeroFueraDeServicio)
    assert [t["llave_idem"] for t in red.bitacora.listar(T)] == ["a"]


# -- límite diario de retiro: por día de negocio en hora de la Ciudad de México ---------------------------
class Banco:
    """El host autorizador con un reloj controlado y una cuenta de prueba (límite diario configurable)."""

    def __init__(self, limite_pesos: int = 9_000):
        from datetime import datetime, timezone

        from till.host import ClienteHost, DatosTerminal, HostAutorizador, TransporteEnProceso

        self.momento = datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)
        self.host = HostAutorizador(reloj=lambda: self.momento)
        self.host.alta_cuenta("CTA-1", dinero.pesos(100_000), limite_retiro_diario=dinero.pesos(limite_pesos))
        self.host.alta_tarjeta(PAN, "CTA-1")
        self.cliente = ClienteHost(TransporteEnProceso(self.host))
        self.dt = DatosTerminal(T, "SUCURSAL0000001", "SUCURSAL SIMULADA")
        self.stan = 0

    def _correr(self, coro):
        import asyncio

        return asyncio.run(coro)

    def retiro(self, pesos: int, cuando=None):
        return self.retiro_en_centavos(dinero.pesos(pesos), cuando)

    def retiro_en_centavos(self, monto: int, cuando=None):
        from till import seguridad

        self.momento = cuando or self.momento
        self.stan += 1
        stan = f"{self.stan:06d}"
        c55 = seguridad.tlv_icc(seguridad.arqc(seguridad.llave_tarjeta(PAN), monto, stan, T, self.stan), self.stan)
        msg = self.cliente.financiera(self.dt, "retiro", monto, stan, iso.campo_7(self.momento),
                                      iso.rrn(self.momento, stan), self.momento, PAN, c55)
        return self._correr(self.cliente.solicitar(dict(msg))).codigo, msg

    def revertir(self, msg, cuando=None):
        self.momento = cuando or self.momento
        self.stan += 1
        orig = {"tipo": "retiro", "monto": int(msg["4"]), "stan": msg["11"], "f7": msg["7"], "rrn": msg["37"],
                "motivo": iso.MotivoReversa.FALLA_DISPOSITIVO}
        rev = self.cliente.reversa(self.dt, orig, f"{self.stan:06d}", self.momento, repeticion=False)
        assert self._correr(self.cliente.revertir(rev))


def _utc(texto: str):
    from datetime import datetime

    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


LIMITES = [
    # nombre, retiros en centavos, códigos esperados (límite diario $9,000)
    ("exactamente_el_limite_en_dos", [500_000, 400_000], ["00", "00"]),
    ("un_peso_de_mas", [500_000, 400_100], ["00", "61"]),
    ("uno_solo_sobre_el_limite", [900_100], ["61"]),
    ("el_rechazado_no_cuenta", [900_100, 900_000], ["61", "00"]),
    ("lleno_y_un_centavo_mas", [900_000, 1], ["00", "61"]),  # el host lleva centavos aunque el cajero entregue pesos
]


@pytest.mark.parametrize("retiros,codigos", [x[1:] for x in LIMITES], ids=[x[0] for x in LIMITES])
def test_limite_diario_en_sus_bordes(retiros, codigos):
    banco = Banco(limite_pesos=9_000)
    assert [banco.retiro_en_centavos(c)[0] for c in retiros] == codigos


DIAS = [
    # nombre, primer retiro (UTC), segundo retiro (UTC), ¿mismo día de negocio?
    ("medianoche_local_es_las_6_utc", "2026-11-24T05:59:59Z", "2026-11-24T06:00:00Z", False),
    ("misma_noche_local", "2026-11-24T00:30:00Z", "2026-11-24T05:30:00Z", True),
    ("cambia_la_fecha_utc_pero_no_la_local", "2026-11-24T23:30:00Z", "2026-11-25T00:30:00Z", True),
    ("verano_sin_horario_de_verano", "2026-07-01T05:59:59Z", "2026-07-01T06:00:00Z", False),
    ("domingo_del_viejo_cambio_de_horario", "2026-04-05T05:59:59Z", "2026-04-05T06:00:00Z", False),
    ("fin_de_anio_local", "2027-01-01T05:59:59Z", "2027-01-01T06:00:00Z", False),
    ("mismo_dia_y_mes_un_anio_despues", "2026-11-24T18:00:00Z", "2027-11-24T18:00:00Z", False),
]


@pytest.mark.parametrize("primero,segundo,mismo_dia", [x[1:] for x in DIAS], ids=[x[0] for x in DIAS])
def test_el_limite_se_reinicia_a_medianoche_de_la_ciudad_de_mexico(primero, segundo, mismo_dia):
    banco = Banco(limite_pesos=9_000)
    assert banco.retiro(9_000, _utc(primero))[0] == "00"
    assert banco.retiro(9_000, _utc(segundo))[0] == ("61" if mismo_dia else "00")


def test_la_reversa_del_mismo_dia_libera_el_limite():
    banco = Banco(limite_pesos=9_000)
    codigo, msg = banco.retiro(9_000, _utc("2026-11-24T18:00:00Z"))
    banco.revertir(msg, _utc("2026-11-24T18:05:00Z"))
    assert codigo == "00" and banco.retiro(9_000)[0] == "00"


def test_la_reversa_de_otro_dia_no_libera_el_limite_de_hoy():
    banco = Banco(limite_pesos=9_000)
    _, ayer = banco.retiro(9_000, _utc("2026-11-24T05:00:00Z"))  # 23:00 del 23-nov en México
    assert banco.retiro(5_000, _utc("2026-11-24T07:00:00Z"))[0] == "00"  # 01:00 del 24-nov
    banco.revertir(ayer)  # la reversa del retiro de ayer llega hoy: devuelve el saldo...
    assert banco.host.saldo("CTA-1") == dinero.pesos(100_000 - 5_000)
    assert banco.retiro(4_001)[0] == "61"  # ...pero hoy ya van 5,000 de 9,000
    assert banco.retiro(4_000)[0] == "00"


def test_la_zona_local_de_iso_8583_es_la_de_la_ciudad_de_mexico():
    """iso.py cae a UTC si no hay base de zonas horarias: eso correría la hora y fecha locales (campos 12/13)
    y el día de negocio. En el motor de la demo lo cuida scripts/humo_motor.mjs (paquete tzdata)."""
    assert str(iso.ZONA_LOCAL) == "America/Mexico_City"
    assert iso.campos_12_13(_utc("2026-07-01T05:59:59Z")) == ("235959", "0630")


def test_el_cajero_rechaza_con_61_sin_mover_efectivo(red, correr):
    pan = "4012888888881881"  # CTA-0003: saldo $250,000 y límite diario de $60,000 en la red de demostración
    c = red.cajero(T)
    for i, pesos in enumerate([40_000, 20_000]):  # 40 billetes de $1,000 es el máximo por entrega
        assert correr(c.operar(Solicitud("retiro", f"l-{i}", monto=dinero.pesos(pesos)), pan=pan)).estado == Estado.CONFIRMADA
    efectivo = red.hardware[T].reciclador.valor_total()
    r = correr(c.operar(Solicitud("retiro", "l-3", monto=dinero.pesos(100)), pan=pan))
    assert r.estado == Estado.RECHAZADA and r.respuesta == "61" and r.entregado == 0
    assert red.hardware[T].reciclador.valor_total() == efectivo
    correr(invariantes(red))
