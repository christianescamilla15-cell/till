"""API HTTP de Till (FastAPI): la usa la demo web de ``web/``.

Expone las operaciones de cada cajero, la inyección de fallas, la traza ISO
8583, el libro mayor, el cuadre y las métricas de la flota. La red de
demostración (banco simulado y cajeros simulados) vive en memoria y su bitácora
en SQLite; arranca limpia en cada inicio porque el hardware simulado no persiste
entre ejecuciones.

Cada visitante de la demo puede tener su propia red mandando el encabezado
``X-Till-Sesion`` (ver :mod:`till.api.sesiones`); sin él se usa la red principal.

Correr:  .venv/bin/uvicorn till.api.app:app --port 8000
Documentación interactiva: http://127.0.0.1:8000/docs

La demo web monta las mismas rutas bajo ``/api`` (``crear_app(prefijo="/api")``,
ver ``api/index.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from till import __version__, dinero
from till.api.sesiones import Entorno, Sesiones, borrar_base
from till.core import CajeroFueraDeServicio, Solicitud, SolicitudInvalida
from till.devices import CorteDeEnergia, FallaNoSoportada, PanNoPermitido
from till.fleet.muestra import correr_muestra
from till.fleet.simulador import ParametrosFlota, simular
from till.host import FallaEnlace
from till.recon import cuadrar
from till.red import CARGA_ESTANDAR, TARJETAS_DEMO, Red, construir_red
from till.seguridad import enmascarar

log = logging.getLogger("till.api")


def directorio_datos() -> Path:
    """Dónde van las bases SQLite: ``TILL_DATOS``, ``/tmp/till`` en Vercel o ``datos/`` del repo.

    En Vercel el sistema de archivos es de sólo lectura salvo el directorio temporal.
    """
    if os.environ.get("TILL_DATOS"):
        return Path(os.environ["TILL_DATOS"])
    if os.environ.get("VERCEL"):
        return Path(tempfile.gettempdir()) / "till"
    return Path(__file__).resolve().parents[2] / "datos"


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


async def entorno_de(request: Request, x_till_sesion: str | None = Header(default=None)) -> Entorno:
    """La red de la sesión (encabezado ``X-Till-Sesion``) o la principal."""
    estado = request.app.state
    if x_till_sesion is None:
        return estado.principal
    try:
        return estado.sesiones.obtener(x_till_sesion)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


async def red_de(ent: Entorno = Depends(entorno_de)) -> Red:
    return ent.red


# Respuestas de error documentadas en el contrato (además del 422 de validación que agrega FastAPI).
NO_EXISTE_CAJERO = {404: {"description": "No existe el cajero indicado"}}
NO_EXISTE_OPERACION = {404: {"description": "No existe la operación indicada"}}
FUERA_DE_SERVICIO = {503: {"description": "El cajero está fuera de servicio por un corte de energía simulado: hay que reiniciarlo"}}
SIN_SIMULACION = {404: {"description": "La sesión todavía no ha corrido una simulación de flota"}}


def cajero_o_404(red: Red, terminal: str):
    if terminal not in red.cajeros:
        raise HTTPException(404, f"no existe el cajero {terminal}")
    return red.cajeros[terminal]


def _rutas() -> APIRouter:
    r = APIRouter()

    # -- general ----------------------------------------------------------------------
    @r.get("/salud", tags=["general"])
    async def salud(request: Request, red: Red = Depends(red_de)) -> dict[str, Any]:
        """Estado de la red: versión, instancia, cajeros fuera de servicio, libro balanceado y reversas pendientes."""
        return {
            "estado": "ok",
            "version": __version__,
            "instancia": request.app.state.instancia,
            "cajeros": len(red.cajeros),
            "fuera_de_servicio": [t for t, c in red.cajeros.items() if c.caido],
            "libro_balanceado": red.libro.verificar()["balanceado"],
            "reversas_pendientes": len(red.bitacora.reversas_pendientes()),
        }

    @r.get("/catalogo", tags=["general"])
    async def catalogo(red: Red = Depends(red_de)) -> dict[str, Any]:
        """Lo que una UI necesita para armar formularios: tarjetas de prueba, comandos y fallas."""
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
    @r.get("/cajeros", tags=["cajeros"])
    async def listar_cajeros(red: Red = Depends(red_de)) -> list[dict[str, Any]]:
        """Cada cajero de la red con su efectivo por casete y el conteo de operaciones por estado."""
        res = []
        for t, c in red.cajeros.items():
            res.append({
                "terminal": t,
                "en_servicio": not c.caido,
                "efectivo": c.reciclador.valor_total(),
                "efectivo_texto": dinero.formatear(c.reciclador.valor_total()),
                "casetes": {d: x.cantidad for d, x in c.reciclador.casetes.items()},
                "operaciones": red.bitacora.conteo_por_estado(t),
            })
        return res

    @r.get("/cajeros/{terminal}", tags=["cajeros"], responses=NO_EXISTE_CAJERO)
    async def detalle_cajero(terminal: str, red: Red = Depends(red_de)) -> dict[str, Any]:
        """Estado de los dispositivos del cajero, sus eventos recientes y sus últimas operaciones."""
        c = cajero_o_404(red, terminal)
        return {
            "terminal": terminal,
            "en_servicio": not c.caido,
            "dispositivos": {n: d.estado() for n, d in c.dispositivos.items()},
            "eventos_recientes": [
                {"dispositivo": e.dispositivo, "evento": e.nombre, "momento": e.momento}
                for d in c.dispositivos.values() for e in list(d.eventos_recientes)[-10:]
            ],
            "ultimas_operaciones": [_con_texto(x) for x in red.bitacora.listar(terminal, 10)],
        }

    @r.get("/cajeros/{terminal}/tablero", tags=["cajeros"], responses=NO_EXISTE_CAJERO)
    async def tablero(
        request: Request, terminal: str, asientos: int = Query(8, ge=1, le=100), red: Red = Depends(red_de)
    ) -> dict[str, Any]:
        """Todo lo que la demo pinta en una sola llamada: efectivo, libro, banco, fallas y reversas."""
        c = cajero_o_404(red, terminal)
        ultimas = red.bitacora.listar(terminal, 1)
        return {
            "instancia": request.app.state.instancia,
            "terminal": terminal,
            "en_servicio": not c.caido,
            "efectivo": c.reciclador.inventario() | {
                "total": c.reciclador.valor_total(),
                "capacidad_por_casete": next(iter(c.reciclador.casetes.values())).capacidad,
            },
            "libro": {"verificacion": red.libro.verificar(), "asientos": red.libro.asientos(limite=asientos)},
            "banco": red.host.resumen(),
            "fallas": {
                "dispositivos": {n: d.fallas.pendientes() for n, d in c.dispositivos.items()},
                "enlace": red.transporte.pendientes(),
            },
            "reversas_pendientes": red.bitacora.reversas_pendientes(terminal),
            "ultima_operacion": ultimas[0]["id"] if ultimas else None,
        }

    @r.post("/cajeros/{terminal}/operaciones", tags=["cajeros"], responses=NO_EXISTE_CAJERO | FUERA_DE_SERVICIO)
    async def operar(
        terminal: str,
        entrada: OperacionEntrada,
        idempotency_key: str | None = Header(default=None),
        red: Red = Depends(red_de),
    ) -> dict[str, Any]:
        """Ejecuta una operación completa (retiro, depósito, pago de crédito o remesa) con una tarjeta de prueba.

        Exige llave de idempotencia (encabezado ``Idempotency-Key`` o ``llave_idem``): repetir la misma solicitud
        devuelve la operación original (``repetida``); reutilizar la llave con otra solicitud da 422. Un corte de
        energía simulado responde 503 con la operación y el siguiente paso (reiniciar)."""
        c = cajero_o_404(red, terminal)
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
            res = await c.operar(s, pan=entrada.pan)
        except (SolicitudInvalida, PanNoPermitido) as e:
            raise HTTPException(422, str(e)) from e
        except CajeroFueraDeServicio as e:
            raise HTTPException(503, str(e)) from e
        except CorteDeEnergia:
            ultima = red.bitacora.listar(terminal, 1)
            raise HTTPException(503, {
                "detalle": "corte de energía simulado: el proceso del cajero murió a media operación",
                "tx_id": ultima[0]["id"] if ultima else None,
                "siguiente_paso": f"POST /cajeros/{terminal}/reiniciar",
            }) from None  # el corte ya quedó en la bitácora; el cliente sólo necesita el 503 y el siguiente paso
        return _con_texto(res.como_dict())

    @r.get("/cajeros/{terminal}/operaciones", tags=["cajeros"], responses=NO_EXISTE_CAJERO)
    async def listar_operaciones(
        terminal: str, limite: int = Query(20, ge=1, le=500), red: Red = Depends(red_de)
    ) -> list[dict[str, Any]]:
        """Las operaciones más recientes del cajero, de la más nueva a la más vieja."""
        cajero_o_404(red, terminal)
        return [_con_texto(x) for x in red.bitacora.listar(terminal, limite)]

    @r.get("/operaciones/{tx_id}", tags=["cajeros"], responses=NO_EXISTE_OPERACION)
    async def detalle_operacion(tx_id: str, red: Red = Depends(red_de)) -> dict[str, Any]:
        """La operación con su historial, su reversa, sus asientos, sus mensajes ISO 8583 y su comprobante."""
        tx = red.bitacora.obtener(tx_id)
        if tx is None:
            raise HTTPException(404, "no existe la operación")
        hw = red.hardware.get(tx["terminal"])
        return {
            "operacion": _con_texto(tx),
            "historial": red.bitacora.historial(tx_id),
            "reversa": red.bitacora.reversa_de(tx_id),
            "asientos": red.libro.asientos(referencia=tx_id),
            "mensajes": red.traza.listar(tx["terminal"], rrn=tx["rrn"]) if tx["rrn"] else [],
            "comprobante": hw.impresora.por_referencia.get(tx_id) if hw else None,
        }

    @r.post("/cajeros/{terminal}/reiniciar", tags=["cajeros"], responses=NO_EXISTE_CAJERO)
    async def reiniciar(terminal: str, red: Red = Depends(red_de)) -> dict[str, Any]:
        """Simula reiniciar el proceso del cajero: arranque de dispositivos y recuperación."""
        cajero_o_404(red, terminal)
        nuevo = red.crear_cajero(terminal)
        return await nuevo.recuperar()

    @r.get("/cajeros/{terminal}/cuadre", tags=["cuadre"], responses=NO_EXISTE_CAJERO | FUERA_DE_SERVICIO)
    async def cuadre(terminal: str, drenar: bool = True, red: Red = Depends(red_de)) -> dict[str, Any]:
        """Cuadre de fin de día a tres bandas: efectivo físico contra libro mayor, libro contra totales del banco y
        lo que explica una diferencia legítima. Con ``drenar`` (por omisión) antes reintenta las reversas pendientes."""
        c = cajero_o_404(red, terminal)
        if c.caido:
            raise HTTPException(503, "el cajero está fuera de servicio: reinícielo antes del cuadre")
        return await cuadrar(c, red.host, drenar_reversas=drenar)

    # -- fallas --------------------------------------------------------------------------
    @r.post("/cajeros/{terminal}/fallas", tags=["fallas"], responses=NO_EXISTE_CAJERO)
    async def programar_falla(terminal: str, entrada: FallaEntrada, red: Red = Depends(red_de)) -> dict[str, Any]:
        """Programa una falla de dispositivo para los siguientes comandos del cajero (atasco, entrega parcial, corte…)."""
        c = cajero_o_404(red, terminal)
        try:
            fp = c.dispositivos[entrada.dispositivo].fallas.programar(
                entrada.falla, entrada.comando, entrada.veces, **entrada.parametros
            )
        except (FallaNoSoportada, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        return {"programada": fp.como_dict(), "dispositivo": entrada.dispositivo}

    @r.get("/cajeros/{terminal}/fallas", tags=["fallas"], responses=NO_EXISTE_CAJERO)
    async def fallas_pendientes(terminal: str, red: Red = Depends(red_de)) -> dict[str, Any]:
        """Fallas de dispositivo programadas que aún no se disparan, por dispositivo."""
        c = cajero_o_404(red, terminal)
        return {n: d.fallas.pendientes() for n, d in c.dispositivos.items()}

    @r.delete("/cajeros/{terminal}/fallas", tags=["fallas"], responses=NO_EXISTE_CAJERO)
    async def limpiar_fallas(terminal: str, red: Red = Depends(red_de)) -> dict[str, str]:
        """Quita todas las fallas de dispositivo programadas en el cajero."""
        c = cajero_o_404(red, terminal)
        for d in c.dispositivos.values():
            d.fallas.limpiar()
        return {"estado": "sin fallas programadas"}

    @r.post("/enlace/fallas", tags=["fallas"])
    async def programar_falla_enlace(entrada: FallaEnlaceEntrada, red: Red = Depends(red_de)) -> dict[str, Any]:
        """El enlace con el banco es compartido por todos los cajeros de la red."""
        red.transporte.programar(entrada.falla, entrada.mti, entrada.veces)
        return {"pendientes": red.transporte.pendientes()}

    @r.get("/enlace/fallas", tags=["fallas"])
    async def fallas_enlace(red: Red = Depends(red_de)) -> dict[str, Any]:
        """Fallas del enlace con el banco programadas que aún no se disparan."""
        return {"pendientes": red.transporte.pendientes()}

    @r.delete("/enlace/fallas", tags=["fallas"])
    async def limpiar_fallas_enlace(red: Red = Depends(red_de)) -> dict[str, str]:
        """Quita las fallas programadas del enlace con el banco."""
        red.transporte.limpiar()
        return {"estado": "sin fallas programadas"}

    # -- reversas y host --------------------------------------------------------------
    @r.get("/reversas", tags=["host"])
    async def reversas_pendientes(red: Red = Depends(red_de)) -> list[dict[str, Any]]:
        """Reversas (0420/0421) encoladas que aún no tienen acuse 0430 del banco."""
        return red.bitacora.reversas_pendientes()

    @r.post("/reversas/drenar", tags=["host"])
    async def drenar_reversas(respetar_espera: bool = False, red: Red = Depends(red_de)) -> dict[str, int]:
        """Un intento por reversa pendiente. Con ``respetar_espera``, sólo las que ya cumplieron su
        espera exponencial: es lo que hace el ciclo de fondo y lo que la demo pide mientras haya
        reversas pendientes, por si la plataforma congela el proceso entre solicitudes."""
        return await red.reversas.drenar(respetar_espera=respetar_espera)

    @r.get("/iso/mensajes", tags=["host"])
    async def mensajes_iso(
        terminal: str | None = None, limite: int = Query(50, ge=1, le=500), red: Red = Depends(red_de)
    ) -> list[dict[str, Any]]:
        """Últimos mensajes ISO 8583 entre los cajeros y el banco (PAN enmascarado, sin campo 55)."""
        return red.traza.listar(terminal, limite=limite)

    @r.post("/host/eco/{terminal}", tags=["host"], responses=NO_EXISTE_CAJERO)
    async def eco(terminal: str, red: Red = Depends(red_de)) -> dict[str, Any]:
        """Mensaje de administración de red 0800/0810 (eco) entre el cajero y el banco."""
        c = cajero_o_404(red, terminal)
        try:
            ok = await c.cliente.eco(c.datos, red.bitacora.siguiente_stan(terminal))
        except Exception as e:  # tiempo agotado, host caído
            return {"0810": False, "error": f"{type(e).__name__}: {e}"}
        return {"0810": ok}

    @r.get("/host/resumen", tags=["host"])
    async def resumen_host(red: Red = Depends(red_de)) -> dict[str, Any]:
        """Lo que ve el banco simulado: saldos, créditos, remesas vigentes, operaciones y mensajes recibidos."""
        return red.host.resumen()

    # -- libro mayor ------------------------------------------------------------------------
    @r.get("/libro-mayor/saldos", tags=["libro mayor"])
    async def saldos(prefijo: str = "", red: Red = Depends(red_de)) -> dict[str, int]:
        """Saldos del libro mayor por cuenta (centavos), opcionalmente filtrados por prefijo de cuenta."""
        return red.libro.saldos(prefijo)

    @r.get("/libro-mayor/asientos", tags=["libro mayor"])
    async def asientos(
        referencia: str | None = None, limite: int = Query(50, ge=1, le=1000), red: Red = Depends(red_de)
    ) -> list[dict[str, Any]]:
        """Asientos del libro mayor con sus movimientos, opcionalmente de una sola operación (``referencia``)."""
        return red.libro.asientos(referencia, limite)

    @r.get("/libro-mayor/verificacion", tags=["libro mayor"])
    async def verificacion(red: Red = Depends(red_de)) -> dict[str, Any]:
        """Verificación de partida doble sobre todo el libro mayor: suma cero y ningún asiento descuadrado."""
        return red.libro.verificar()

    # -- flota ------------------------------------------------------------------------------
    @r.post("/flota/simulacion", tags=["flota"])
    async def simular_flota(entrada: SimulacionEntrada, ent: Entorno = Depends(entorno_de)) -> dict[str, Any]:
        """Corre el simulador agregado de flota (1 a 15 000 cajeros) y guarda sus métricas para la sesión."""
        res = await simular(ParametrosFlota(**entrada.model_dump()))
        ent.flota = res
        return res

    @r.get("/flota/metricas", tags=["flota"], responses=SIN_SIMULACION)
    async def metricas_flota(ent: Entorno = Depends(entorno_de)) -> dict[str, Any]:
        """Métricas de la última simulación de flota de la sesión."""
        if ent.flota is None:
            raise HTTPException(404, "todavía no se ha corrido una simulación (POST /flota/simulacion)")
        return ent.flota

    @r.post("/flota/muestra", tags=["flota"])
    async def muestra(entrada: MuestraEntrada) -> dict[str, Any]:
        """Corre la máquina de estados real en N cajeros con fallas aleatorias (base temporal aparte)."""
        with tempfile.TemporaryDirectory(prefix="till-muestra-") as d:
            return await correr_muestra(Path(d) / "muestra.sqlite3", **entrada.model_dump())

    return r


def crear_app(
    ruta_db: str | Path | None = None,
    cajeros: int = 3,
    reenvio_en_fondo: bool = True,
    intervalo_reenvio: float = 1.0,
    prefijo: str = "",
    cajeros_por_sesion: int = 1,
    max_sesiones: int = 50,
    documentacion: bool = True,
) -> FastAPI:
    """Arma la API.

    ``prefijo``: dónde se montan las rutas (``""`` o ``"/api"``). La red principal usa ``ruta_db``;
    sin ella, ``directorio_datos()/till-demo.sqlite3``, que se borra al arrancar (la demo arranca
    limpia). Las redes por sesión van a ``sesiones/`` junto a la base principal.
    ``documentacion=False`` quita /docs y /redoc (cargan Swagger desde una CDN, que la CSP
    de la demo no permite); el contrato ``openapi.json`` se queda.
    """

    @contextlib.asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        ruta = Path(ruta_db) if ruta_db else directorio_datos() / "till-demo.sqlite3"
        if ruta_db is None:
            borrar_base(ruta)
        principal = Entorno(construir_red(ruta, cajeros=cajeros))
        sesiones = Sesiones(ruta.parent / "sesiones", cajeros=cajeros_por_sesion, maximo=max_sesiones)
        app.state.principal = principal
        app.state.sesiones = sesiones
        app.state.instancia = uuid.uuid4().hex[:12]
        detener = asyncio.Event()

        async def reenviar() -> None:
            """Ciclo de fondo: reintenta las reversas pendientes de todas las redes, respetando su espera."""
            while not detener.is_set():
                for ent in [principal, *sesiones.entornos()]:
                    try:
                        await ent.red.reversas.drenar(respetar_espera=True)
                    except Exception:  # el ciclo de reenvío nunca debe morir por un error puntual
                        log.exception("error en el ciclo de reversas")
                sesiones.purgar()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(detener.wait(), intervalo_reenvio)

        tarea = asyncio.create_task(reenviar()) if reenvio_en_fondo else None
        try:
            yield
        finally:
            detener.set()
            if tarea:
                await tarea
            sesiones.cerrar()
            principal.red.cerrar()

    prefijo = prefijo.rstrip("/")
    app = FastAPI(
        title="Till",
        version=__version__,
        description="Red de cajeros de sucursal automatizados: implementación de referencia (Etapa 1, todo simulado).",
        lifespan=ciclo_de_vida,
        docs_url=f"{prefijo}/docs" if documentacion else None,
        redoc_url=f"{prefijo}/redoc" if documentacion else None,
        openapi_url=f"{prefijo}/openapi.json",
    )
    app.include_router(_rutas(), prefix=prefijo)
    return app


app = crear_app()
