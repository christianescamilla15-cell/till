"""Traza ISO 8583: lo que la UI muestra del diálogo con el banco, sin datos sensibles."""

from till.core import Solicitud
from till.host import FallaEnlace
from till.red import construir_red

T = "TILL0001"
PAN = "4111111111111111"


def _red(tmp_path):
    return construir_red(tmp_path / "traza.sqlite3", cajeros=1)


def test_retiro_deja_0200_y_0210_sin_pan_ni_chip(tmp_path, correr):
    red = _red(tmp_path)
    r = correr(red.cajero(T).operar(Solicitud("retiro", "t1", monto=150_000), pan=PAN))
    tx = red.bitacora.obtener(r.tx_id)
    msgs = red.traza.listar(T, rrn=tx["rrn"])
    assert [m["mti"] for m in msgs] == ["0200"]
    m = msgs[0]
    assert m["resultado"] == "respuesta" and m["respuesta"]["t"] == "0210" and m["respuesta"]["39"] == "00"
    assert m["solicitud"]["2"] == "411111******1111" and "55" not in m["solicitud"]
    assert PAN not in repr(red.traza.listar())
    red.cerrar()


def test_respuesta_perdida_queda_en_la_traza_y_la_reversa_la_sigue(tmp_path, correr):
    red = _red(tmp_path)
    red.transporte.programar(FallaEnlace.RESPUESTA_PERDIDA, "0200")
    r = correr(red.cajero(T).operar(Solicitud("retiro", "t2", monto=50_000), pan=PAN))
    assert r.estado == "REVERTIDA"
    msgs = red.traza.listar(T, rrn=red.bitacora.obtener(r.tx_id)["rrn"])
    assert [m["mti"] for m in msgs] == ["0200", "0420"]
    assert msgs[0]["resultado"] == "tiempo_agotado" and msgs[0]["respuesta"] is None
    assert msgs[0]["respuesta_perdida"]["39"] == "00"  # el banco sí aprobó: por eso hace falta la reversa
    assert msgs[1]["respuesta"]["t"] == "0430"
    red.cerrar()


def test_host_caido_anota_cada_reintento_hasta_el_0430(tmp_path, correr):
    red = _red(tmp_path)
    red.transporte.programar(FallaEnlace.HOST_CAIDO, None, veces=3)  # 0200, 0420 y una 0421
    r = correr(red.cajero(T).operar(Solicitud("retiro", "t3", monto=50_000), pan=PAN))
    assert r.reversa_pendiente
    correr(red.reversas.drenar())  # 0421: enlace todavía caído
    correr(red.reversas.drenar())  # 0421: llega el 0430
    msgs = red.traza.listar(T, rrn=red.bitacora.obtener(r.tx_id)["rrn"])
    assert [(m["mti"], m["resultado"]) for m in msgs] == [
        ("0200", "host_caido"), ("0420", "host_caido"), ("0421", "host_caido"), ("0421", "respuesta"),
    ]
    assert msgs[-1]["respuesta"]["t"] == "0430"
    assert red.bitacora.obtener(r.tx_id)["estado"] == "REVERTIDA"
    red.cerrar()


def test_comprobante_ligado_a_su_operacion(tmp_path, correr):
    red = _red(tmp_path)
    r = correr(red.cajero(T).operar(Solicitud("retiro", "t4", monto=20_000), pan=PAN))
    texto = red.cajero(T).impresora.por_referencia[r.tx_id]
    assert "MONTO $200.00" in texto and "411111******1111" in texto
    red.cerrar()
