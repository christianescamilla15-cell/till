from datetime import datetime, timezone

import pytest

from till import dinero, seguridad
from till.host import (
    ClienteHost,
    DatosTerminal,
    FallaEnlace,
    HostAutorizador,
    ServidorHostTcp,
    TiempoAgotadoHost,
    TransporteEnProceso,
    TransporteTcp,
)
from till.host import iso

PAN = "4111111111111111"
DT = DatosTerminal("TILL0001", "SUCURSAL0000001", "SUCURSAL CENTRO CDMX")
MOMENTO = datetime(2026, 11, 17, 18, 30, 0, tzinfo=timezone.utc)


def _host():
    h = HostAutorizador()
    h.alta_cuenta("CTA-1", dinero.pesos(10_000), limite_retiro_diario=dinero.pesos(9_000))
    h.alta_tarjeta(PAN, "CTA-1")
    h.alta_credito("CRED-1", dinero.pesos(3_000))
    return h


def _msg(cliente, tipo, monto, stan, destino=None, pan=PAN):
    f7 = iso.campo_7(MOMENTO)
    llave = seguridad.llave_tarjeta(pan)
    c55 = seguridad.tlv_icc(seguridad.arqc(llave, monto, stan, DT.terminal, 1), 1)
    return cliente.financiera(DT, tipo, monto, stan, f7, iso.rrn(MOMENTO, stan), MOMENTO, pan, c55, destino)


def test_codificacion_ida_y_vuelta():
    cliente = ClienteHost(TransporteEnProceso(_host()))
    msg = _msg(cliente, "retiro", dinero.pesos(500), "000001")
    raw = iso.codificar(msg)
    assert raw[:4] == b"0200"
    dec = iso.decodificar(raw)
    assert dec["4"] == "000000050000" and dec["41"] == "TILL0001" and dec["49"] == "484"
    assert iso.sin_datos_sensibles(dec)["2"] == "411111******1111"


def test_retiro_aprobado_y_fondos_insuficientes(correr):
    h = _host()
    cliente = ClienteHost(TransporteEnProceso(h))
    r = correr(cliente.solicitar(_msg(cliente, "retiro", dinero.pesos(1_000), "000001")))
    assert r.aprobada and r.autorizacion and r.saldo == dinero.pesos(9_000)
    r = correr(cliente.solicitar(_msg(cliente, "retiro", dinero.pesos(9_500), "000002")))
    assert r.codigo == "51"
    r = correr(cliente.solicitar(_msg(cliente, "retiro", dinero.pesos(8_500), "000003")))
    assert r.codigo == "61"  # excede el límite diario (1,000 + 8,500 > 9,000)
    assert h.saldo("CTA-1") == dinero.pesos(9_000)


def test_criptograma_invalido_se_rechaza(correr):
    h = _host()
    cliente = ClienteHost(TransporteEnProceso(h))
    msg = _msg(cliente, "retiro", dinero.pesos(100), "000001")
    msg["4"] = iso.monto(dinero.pesos(900))  # alterar el monto invalida el ARQC
    assert correr(cliente.solicitar(msg)).codigo == "05"
    assert h.saldo("CTA-1") == dinero.pesos(10_000)


def test_0200_repetida_no_carga_dos_veces(correr):
    h = _host()
    t = TransporteEnProceso(h)
    cliente = ClienteHost(t)
    msg = _msg(cliente, "retiro", dinero.pesos(700), "000010")
    t.programar(FallaEnlace.RESPUESTA_PERDIDA, mti="0200")
    with pytest.raises(TiempoAgotadoHost):
        correr(cliente.solicitar(msg))
    r = correr(cliente.solicitar(msg))  # retransmisión
    assert r.aprobada
    assert h.saldo("CTA-1") == dinero.pesos(9_300)


def test_reversa_total_parcial_e_idempotente(correr):
    h = _host()
    cliente = ClienteHost(TransporteEnProceso(h))
    m1 = _msg(cliente, "retiro", dinero.pesos(1_000), "000001")
    correr(cliente.solicitar(m1))
    orig = {"tipo": "retiro", "monto": dinero.pesos(1_000), "stan": "000001", "f7": m1["7"], "rrn": m1["37"],
            "reemplazo": dinero.pesos(400), "motivo": iso.MotivoReversa.PARCIAL}
    rev = cliente.reversa(DT, orig, "000002", MOMENTO, repeticion=False)
    assert rev["95"].startswith("000000040000")
    assert correr(cliente.revertir(rev))
    assert h.saldo("CTA-1") == dinero.pesos(9_600)  # sólo se cobra lo entregado
    rep = cliente.reversa(DT, orig, "000003", MOMENTO, repeticion=True)
    assert rep["t"] == "0421"
    assert correr(cliente.revertir(rep))
    assert h.saldo("CTA-1") == dinero.pesos(9_600)  # el aviso repetido no abona de nuevo
    assert h.totales_terminal("TILL0001")["retiro"] == dinero.pesos(400)


def test_reversa_sin_original_bloquea_la_0200_tardia(correr):
    h = _host()
    cliente = ClienteHost(TransporteEnProceso(h))
    m = _msg(cliente, "retiro", dinero.pesos(500), "000005")
    orig = {"tipo": "retiro", "monto": dinero.pesos(500), "stan": "000005", "f7": m["7"], "rrn": m["37"],
            "motivo": iso.MotivoReversa.SIN_RESPUESTA}
    assert correr(cliente.revertir(cliente.reversa(DT, orig, "000006", MOMENTO, False)))
    assert correr(cliente.solicitar(m)).codigo == "12"
    assert h.saldo("CTA-1") == dinero.pesos(10_000)


def test_pago_de_credito_y_remesa(correr):
    h = _host()
    cliente = ClienteHost(TransporteEnProceso(h))
    assert correr(cliente.solicitar(_msg(cliente, "pago_credito", dinero.pesos(1_000), "000001", "CRED-1"))).aprobada
    assert h.creditos["CRED-1"] == dinero.pesos(2_000)
    assert correr(cliente.solicitar(_msg(cliente, "pago_credito", dinero.pesos(5_000), "000002", "CRED-1"))).codigo == "13"
    r = correr(cliente.solicitar(_msg(cliente, "remesa", dinero.pesos(2_000), "000003", "BENEF-77")))
    assert r.aprobada and h.remesas[r.autorizacion].monto == dinero.pesos(2_000)


def test_eco_y_transporte_tcp(correr):
    h = _host()

    async def flujo():
        srv = ServidorHostTcp(h)
        puerto = await srv.iniciar()
        t = TransporteTcp("127.0.0.1", puerto)
        cliente = ClienteHost(t)
        try:
            assert await cliente.eco(DT, "000001")
            r = await cliente.solicitar(_msg(cliente, "deposito", dinero.pesos(250), "000002"))
            return r
        finally:
            await t.cerrar()
            await srv.detener()

    r = correr(flujo())
    assert r.aprobada and h.saldo("CTA-1") == dinero.pesos(10_250)
