"""API HTTP de Till (FastAPI), pensada para una UI futura.

Expone las operaciones de cada cajero, la inyección de fallas, el libro mayor,
el cuadre y las métricas de la flota. La red de demostración (banco simulado y
cajeros simulados) vive en memoria y su bitácora en SQLite; arranca limpia en
cada inicio porque el hardware simulado no persiste entre ejecuciones.

Correr:  .venv/bin/uvicorn till.api.app:app --port 8000
Documentación interactiva: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from till import __version__, dinero
from till.core import CajeroFueraDeServicio, Solicitud, SolicitudInvalida
from till.devices import CorteDeEnergia, FallaNoSoportada, PanNoPermitido
from till.fleet.muestra import correr_muestra
from till.fleet.simulador import ParametrosFlota, simular
from till.host import FallaEnlace
from till.recon import cuadrar
from till.red import CARGA_ESTANDAR, TARJETAS_DEMO, Red, construir_red
from till.seguridad import enmascarar

log = logging.getLogger("till.api")

RUTA_DEMO = Path(__file__).resolve().parents[2] / "datos" / "till-demo.sqlite3"


# -- modelos de entrada --------------------------------------------------------------
class OperacionEntrada(BaseModel):
    tipo: Literal["retiro", "deposito", "pago_credito", "remesa"]
    pan: str = Field(description="PAN de prueba (simula insertar la tarjeta); nunca se guarda")
    monto_pesos: int | None = Field(default=None, gt=0, description="sólo retiro")
    billetes: dict[int, int] | None = Field(default=None, description="sólo ingresos: denominación -> cantidad")
    destino: str | None = Field(default=None, description="crédito (pago) o beneficiario (remesa)")
    llave_idem: str | None = Field(default=None, description="alternativa al encabezado Idempotency-Key")


class FallaEntrada(BaseModel):
    dispositivo: Literal["efectivo", "tarjeta", "impresora", "camara"]
    falla: str
    comando: str
    veces: int = Field(default=1, ge=1, le=100)
    parametros: dict[str, Any] = Field(default_factory=dict)


class FallaEnlaceEntrada(BaseModel):
    falla: FallaEnlace
    mti: str | None = Field(default=None, pattern=r"^\d{4}$")
    veces: int = Field(default=1, ge=1, le=100)


class SimulacionEntrada(BaseModel):
    cajeros: int = Field(default=200, ge=1, le=15_000)
    horas: int = Field(default=24, ge=1, le=336)
    semilla: int = 2026
    tx_por_dia: float = Field(default=150.0, gt=0, le=2_000)
    prob_falla_dispositivo: float = Field(default=0.0012, ge=0, le=0.5)
    prob_falla_enlace: float = Field(default=0.001, ge=0, le=0.5)
    mttr_horas: float = Field(default=3.0, gt=0, le=72)


class MuestraEntrada(BaseModel):
    cajeros: int = Field(default=10, ge=1, le=50)
    operaciones_por_cajero: int = Field(default=10, ge=1, le=50)
    prob_falla: float = Field(default=0.08, ge=0, le=1)
    prob_falla_enlace: float = Field(default=0.03, ge=0, le=1)
    semilla: int = 7


# -- utilidades ---------------------------------------------------------------------
def _con_texto(d: dict[str, Any], campos: tuple[str, ...] = ("monto", "entregado", "aclaracion")) -> dict[str, Any]:
    for c in campos:
        if c in d and isinstance(d[c], int):
            d[f"{c}_texto"] = dinero.formatear(d[c])
    return d


def crear_app(
    ruta_db: str | Path | None = None,
    cajeros: int = 3,
    reenvio_en_fondo: bool = True,
    intervalo_reenvio: float = 1.0,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        ruta = Path(ruta_db) if ruta_db else RUTA_DEMO
        if ruta_db is None:
            for sufijo in ("", "-wal", "-shm"):  # la demo arranca limpia
                Path(str(ruta) + sufijo).unlink(missing_ok=True)
        red = construir_red(ruta, cajeros=cajeros)
        app.state.red = red
        app.state.flota = None
        detener = asyncio.Event()
        tarea = asyncio.create_task(red.reversas.correr(detener, intervalo_reenvio)) if reenvio_en_fondo else None
        try:
            yield
        finally:
            detener.set()
            if tarea:
                await tarea
            red.cerrar()

    app = FastAPI(
        title="Till",
        version=__version__,
        description="Red de cajeros de sucursal automatizados: implementación de referencia (Etapa 1, todo simulado).",
        lifespan=ciclo_de_vida,
    )

    def red_de(app_: FastAPI = app) -> Red:
        return app_.state.red

    def cajero_o_404(terminal: str):
        red = red_de()
        if terminal not in red.cajeros:
            raise HTTPException(404, f"no existe el cajero {terminal}")
        return red.cajeros[terminal]

    # -- general ----------------------------------------------------------------------
    @app.get("/salud", tags=["general"])
    async def salud() -> dict[str, Any]:
        red = red_de()
        return {
            "estado": "ok",
            "version": __version__,
            "cajeros": len(red.cajeros),
            "fuera_de_servicio": [t for t, c in red.cajeros.items() if c.caido],
            "libro_balanceado": red.libro.verificar()["balanceado"],
            "reversas_pendientes": len(red.bitacora.reversas_pendientes()),
        }

    @app.get("/catalogo", tags=["general"])
    async def catalogo() -> dict[str, Any]:
        """Lo que una UI necesita para armar formularios: tarjetas de prueba, comandos y fallas."""
        red = red_de()
        t = next(iter(red.cajeros.values()))
        return {
            "tarjetas_de_prueba": [{"pan": pan, "enmascarado": enmascarar(pan), "cuenta": cta} for pan, cta, _ in TARJETAS_DEMO],
            "creditos": list(red.host.creditos),
            "denominaciones": list(dinero.DENOMINACIONES_MXN),
            "carga_estandar": CARGA_ESTANDAR,
            "fallas_por_dispositivo": {
                nombre: {cmd: sorted(f.value for f in fallas) for cmd, fallas in dev.FALLAS_SOPORTADAS.items() if fallas}
                for nombre, dev in t.dispositivos.items()
            },
            "fallas_de_enlace": [f.value for f in FallaEnlace],
        }

    # -- cajeros ---------------------------------------------------------------------
    @app.get("/cajeros", tags=["cajeros"])
    async def listar_cajeros() -> list[dict[str, Any]]:
        r = []
        for t, c in red_de().cajeros.items():
            r.append({
                "terminal": t,
                "en_servicio": not c.caido,
                "efectivo": c.reciclador.valor_total(),
                "efectivo_texto": dinero.formatear(c.reciclador.valor_total()),
                "casetes": {d: x.cantidad for d, x in c.reciclador.casetes.items()},
                "operaciones": red_de().bitacora.conteo_por_estado(t),
            })
        return r

    @app.get("/cajeros/{terminal}", tags=["cajeros"])
    async def detalle_cajero(terminal: str) -> dict[str, Any]:
        c = cajero_o_404(terminal)
        return {
            "terminal": terminal,
            "en_servicio": not c.caido,
            "dispositivos": {n: d.estado() for n, d in c.dispositivos.items()},
            "eventos_recientes": [
                {"dispositivo": e.dispositivo, "evento": e.nombre, "momento": e.momento}
                for d in c.dispositivos.values() for e in list(d.eventos_recientes)[-10:]
            ],
            "ultimas_operaciones": [_con_texto(x) for x in red_de().bitacora.listar(terminal, 10)],
        }

    @app.post("/cajeros/{terminal}/operaciones", tags=["cajeros"])
    async def operar(
        terminal: str,
        entrada: OperacionEntrada,
        idempotency_key: str | None = Header(default=None),
    ) -> dict[str, Any]:
        c = cajero_o_404(terminal)
        llave = idempotency_key or entrada.llave_idem
        if not llave:
            raise HTTPException(422, "falta la llave de idempotencia (encabezado Idempotency-Key o llave_idem)")
        s = Solicitud(
            tipo=entrada.tipo,
            llave_idem=llave,
            monto=dinero.pesos(entrada.monto_pesos or 0),
            billetes=dict(entrada.billetes or {}),
            destino=entrada.destino,
        )
        try:
            r = await c.operar(s, pan=entrada.pan)
        except (SolicitudInvalida, PanNoPermitido) as e:
            raise HTTPException(422, str(e)) from e
        except CajeroFueraDeServicio as e:
            raise HTTPException(503, str(e)) from e
        except CorteDeEnergia:
            ultima = red_de().bitacora.listar(terminal, 1)
            raise HTTPException(503, {
                "detalle": "corte de energía simulado: el proceso del cajero murió a media operación",
                "tx_id": ultima[0]["id"] if ultima else None,
                "siguiente_paso": f"POST /cajeros/{terminal}/reiniciar",
            })
        return _con_texto(r.como_dict())

    @app.get("/cajeros/{terminal}/operaciones", tags=["cajeros"])
    async def listar_operaciones(terminal: str, limite: int = Query(20, ge=1, le=500)) -> list[dict[str, Any]]:
        cajero_o_404(terminal)
        return [_con_texto(x) for x in red_de().bitacora.listar(terminal, limite)]

    @app.get("/operaciones/{tx_id}", tags=["cajeros"])
    async def detalle_operacion(tx_id: str) -> dict[str, Any]:
        red = red_de()
        tx = red.bitacora.obtener(tx_id)
        if tx is None:
            raise HTTPException(404, "no existe la operación")
        return {
            "operacion": _con_texto(tx),
            "historial": red.bitacora.historial(tx_id),
            "reversa": red.bitacora.reversa_de(tx_id),
            "asientos": red.libro.asientos(referencia=tx_id),
        }

    @app.post("/cajeros/{terminal}/reiniciar", tags=["cajeros"])
    async def reiniciar(terminal: str) -> dict[str, Any]:
        """Simula reiniciar el proceso del cajero: arranque de dispositivos y recuperación."""
        cajero_o_404(terminal)
        nuevo = red_de().crear_cajero(terminal)
        return await nuevo.recuperar()

    @app.get("/cajeros/{terminal}/cuadre", tags=["cuadre"])
    async def cuadre(terminal: str, drenar: bool = True) -> dict[str, Any]:
        c = cajero_o_404(terminal)
        if c.caido:
            raise HTTPException(503, "el cajero está fuera de servicio: reinícielo antes del cuadre")
        return await cuadrar(c, red_de().host, drenar_reversas=drenar)

    # -- fallas --------------------------------------------------------------------------
    @app.post("/cajeros/{terminal}/fallas", tags=["fallas"])
    async def programar_falla(terminal: str, entrada: FallaEntrada) -> dict[str, Any]:
        c = cajero_o_404(terminal)
        try:
            fp = c.dispositivos[entrada.dispositivo].fallas.programar(
                entrada.falla, entrada.comando, entrada.veces, **entrada.parametros
            )
        except (FallaNoSoportada, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        return {"programada": fp.como_dict(), "dispositivo": entrada.dispositivo}

    @app.get("/cajeros/{terminal}/fallas", tags=["fallas"])
    async def fallas_pendientes(terminal: str) -> dict[str, Any]:
        c = cajero_o_404(terminal)
        return {n: d.fallas.pendientes() for n, d in c.dispositivos.items()}

    @app.delete("/cajeros/{terminal}/fallas", tags=["fallas"])
    async def limpiar_fallas(terminal: str) -> dict[str, str]:
        c = cajero_o_404(terminal)
        for d in c.dispositivos.values():
            d.fallas.limpiar()
        return {"estado": "sin fallas programadas"}

    @app.post("/enlace/fallas", tags=["fallas"])
    async def programar_falla_enlace(entrada: FallaEnlaceEntrada) -> dict[str, Any]:
        """El enlace con el banco es compartido por todos los cajeros de la simulación."""
        red_de().transporte.programar(entrada.falla, entrada.mti, entrada.veces)
        return {"pendientes": red_de().transporte.pendientes()}

    @app.delete("/enlace/fallas", tags=["fallas"])
    async def limpiar_fallas_enlace() -> dict[str, str]:
        red_de().transporte.limpiar()
        return {"estado": "sin fallas programadas"}

    # -- reversas y host --------------------------------------------------------------
    @app.get("/reversas", tags=["host"])
    async def reversas_pendientes() -> list[dict[str, Any]]:
        return red_de().bitacora.reversas_pendientes()

    @app.post("/reversas/drenar", tags=["host"])
    async def drenar_reversas() -> dict[str, int]:
        return await red_de().reversas.drenar()

    @app.post("/host/eco/{terminal}", tags=["host"])
    async def eco(terminal: str) -> dict[str, Any]:
        c = cajero_o_404(terminal)
        try:
            ok = await c.cliente.eco(c.datos, red_de().bitacora.siguiente_stan(terminal))
        except Exception as e:  # tiempo agotado, host caído
            return {"0810": False, "error": f"{type(e).__name__}: {e}"}
        return {"0810": ok}

    @app.get("/host/resumen", tags=["host"])
    async def resumen_host() -> dict[str, Any]:
        return red_de().host.resumen()

    # -- libro mayor ------------------------------------------------------------------------
    @app.get("/libro-mayor/saldos", tags=["libro mayor"])
    async def saldos(prefijo: str = "") -> dict[str, int]:
        return red_de().libro.saldos(prefijo)

    @app.get("/libro-mayor/asientos", tags=["libro mayor"])
    async def asientos(referencia: str | None = None, limite: int = Query(50, ge=1, le=1000)) -> list[dict[str, Any]]:
        return red_de().libro.asientos(referencia, limite)

    @app.get("/libro-mayor/verificacion", tags=["libro mayor"])
    async def verificacion() -> dict[str, Any]:
        return red_de().libro.verificar()

    # -- flota ------------------------------------------------------------------------------
    @app.post("/flota/simulacion", tags=["flota"])
    async def simular_flota(entrada: SimulacionEntrada) -> dict[str, Any]:
        r = await simular(ParametrosFlota(**entrada.model_dump()))
        app.state.flota = r
        return r

    @app.get("/flota/metricas", tags=["flota"])
    async def metricas_flota() -> dict[str, Any]:
        if app.state.flota is None:
            raise HTTPException(404, "todavía no se ha corrido una simulación (POST /flota/simulacion)")
        return app.state.flota

    @app.post("/flota/muestra", tags=["flota"])
    async def muestra(entrada: MuestraEntrada) -> dict[str, Any]:
        """Corre la máquina de estados real en N cajeros con fallas aleatorias (base temporal aparte)."""
        with tempfile.TemporaryDirectory(prefix="till-muestra-") as d:
            return await correr_muestra(Path(d) / "muestra.sqlite3", **entrada.model_dump())

    return app


app = crear_app()
