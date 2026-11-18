"""Reciclador de efectivo simulado (entrega y recepción de billetes MXN).

Un reciclador usa los mismos casetes para entregar y para recibir: lo que un
cliente deposita sirve para el siguiente retiro. Ubicaciones físicas:

* ``casetes``   uno por denominación (20, 50, 100, 200, 500, 1000).
* ``transporte`` billetes ya contados que aún no se presentan al cliente.
* ``escrow``    billetes depositados que esperan confirmación.
* ``rechazo``   billetes purgados por atasco o error de entrega.
* ``retenido``  billetes que el cliente no tomó o depósitos que no se pudieron devolver.
* ``excedente`` billetes depositados cuando su casete está lleno.

El dispositivo guarda en su memoria no volátil (``nvram``) la fase de cada
operación por referencia, como hacen los equipos reales con sus contadores.
Eso permite que, tras un corte de energía, la aplicación pregunte qué pasó
(``GetPresentStatus`` / ``GetCashInStatus``) y decida si confirma o revierte.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from till import dinero
from till.devices.base import (
    CodigoTerminacion as C,
    CorteDeEnergia,
    Dispositivo,
    Falla,
    FallaProgramada,
    Terminacion,
)
from till.dinero import DENOMINACIONES_MXN, Mezcla


@dataclass
class Casete:
    denominacion: int
    cantidad: int
    capacidad: int


class RecicladorEfectivo(Dispositivo):
    clase = "CashRecycler"
    FALLAS_SOPORTADAS = {
        "CashDispenser.Denominate": set(),
        "CashDispenser.Dispense": {Falla.ATASCO, Falla.ENTREGA_PARCIAL, Falla.TIEMPO_AGOTADO, Falla.CORTE_ENERGIA},
        "CashDispenser.Present": {Falla.ATASCO, Falla.NO_RETIRADO, Falla.CORTE_ENERGIA},
        "CashDispenser.GetPresentStatus": set(),
        "CashAcceptor.CashInStart": set(),
        "CashAcceptor.CashIn": {Falla.ATASCO, Falla.TIEMPO_AGOTADO},
        "CashAcceptor.CashInEnd": {Falla.ATASCO, Falla.TIEMPO_AGOTADO, Falla.CORTE_ENERGIA},
        "CashAcceptor.CashInRollback": {Falla.ATASCO},
        "CashAcceptor.GetCashInStatus": set(),
        "CashManagement.GetCashUnitInfo": set(),
        "Storage.Replenish": set(),
    }

    def __init__(
        self,
        ident: str,
        carga: Mezcla | None = None,
        capacidad: int = 2500,
        max_billetes: int = dinero.MAX_BILLETES_POR_ENTREGA,
        latencia: float = 0.0,
    ):
        super().__init__(ident, latencia)
        carga = carga or {}
        self.casetes: dict[int, Casete] = {
            d: Casete(d, int(carga.get(d, 0)), capacidad) for d in DENOMINACIONES_MXN
        }
        self.max_billetes = max_billetes
        self.rechazo: Mezcla = {}
        self.retenido: Mezcla = {}
        self.excedente: Mezcla = {}
        self.transporte: Mezcla = {}
        self.ref_transporte: str | None = None
        self.escrow: Mezcla = {}
        self.ref_escrow: str | None = None
        self.nvram: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # -- utilidades --------------------------------------------------------
    def disponibles(self) -> Mezcla:
        return {d: c.cantidad for d, c in self.casetes.items() if c.cantidad > 0}

    def _registro(self, referencia: str, **cambios: Any) -> dict[str, Any]:
        reg = self.nvram.setdefault(referencia, {})
        reg.update(cambios)
        self.nvram.move_to_end(referencia)
        while len(self.nvram) > 500:
            self.nvram.popitem(last=False)
        return reg

    def _a_bin(self, bin_: Mezcla, mezcla: Mezcla) -> None:
        for d, n in mezcla.items():
            bin_[d] = bin_.get(d, 0) + n

    def estado(self) -> dict[str, Any]:
        base = super().estado()
        base.update(self.inventario())
        return base

    def inventario(self) -> dict[str, Any]:
        return {
            "casetes": {d: c.cantidad for d, c in self.casetes.items()},
            "rechazo": dict(self.rechazo),
            "retenido": dict(self.retenido),
            "excedente": dict(self.excedente),
            "transporte": dict(self.transporte),
            "escrow": dict(self.escrow),
        }

    def valor_total(self) -> int:
        inv = self.inventario()
        return sum(dinero.valor(m) for m in inv.values())

    # -- arranque tras corte ----------------------------------------------
    async def encender(self) -> dict[str, Any]:
        """Al volver la energía: purga transporte y retiene el escrow."""
        resumen: dict[str, Any] = {}
        if self.transporte:
            ref = self.ref_transporte or "?"
            reg = self.nvram.get(ref, {})
            m = dict(self.transporte)
            if reg.get("fase") == "presentado":
                # Estaban en la boca del cajero y nadie confirmó que se tomaron: se retraen.
                self._a_bin(self.retenido, m)
                self._registro(ref, fase="retenido", retenido=dinero.sumar(reg.get("retenido", {}), m))
                resumen["retenido"] = m
            else:
                self._a_bin(self.rechazo, m)
                self._registro(ref, fase="no_presentado", rechazo=dinero.sumar(reg.get("rechazo", {}), m))
                resumen["rechazo"] = m
            self.transporte, self.ref_transporte = {}, None
        if self.escrow:
            ref = self.ref_escrow or "?"
            m = dict(self.escrow)
            self._a_bin(self.retenido, m)
            self._registro(ref, fase="retenido", retenido=m)
            self.escrow, self.ref_escrow = {}, None
            resumen["escrow_retenido"] = m
        await super().encender()
        return resumen

    # -- entrega -----------------------------------------------------------
    async def _cmd_CashDispenser_Denominate(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        mezcla = dinero.calcular_mezcla(int(datos["monto"]), self.disponibles(), self.max_billetes)
        if mezcla is None:
            return Terminacion("CashDispenser.Denominate", id_sol, C.ERROR_COMANDO, "notDispensable")
        return Terminacion("CashDispenser.Denominate", id_sol, C.EXITO, datos={"mezcla": mezcla})

    async def _cmd_CashDispenser_Dispense(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CashDispenser.Dispense"
        ref = str(datos["referencia"])
        mezcla = dinero.limpiar(datos["mezcla"])
        if self.transporte:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "transportNotEmpty")
        if not mezcla or any(self.casetes[d].cantidad < n for d, n in mezcla.items()):
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "notDispensable")
        # Los billetes salen de los casetes hacia el transporte.
        for d, n in mezcla.items():
            self.casetes[d].cantidad -= n
        self.transporte, self.ref_transporte = dict(mezcla), ref
        self._registro(ref, tipo="entrega", fase="en_transporte", solicitado=mezcla, entregado={}, rechazo={}, retenido={})

        if falla and falla.falla is Falla.CORTE_ENERGIA:
            self._apagar()
            raise CorteDeEnergia(f"{self.ident}: corte de energía durante {cmd}")
        if falla and falla.falla in (Falla.ATASCO, Falla.TIEMPO_AGOTADO):
            self._a_bin(self.rechazo, mezcla)
            self.transporte, self.ref_transporte = {}, None
            self._registro(ref, fase="no_presentado", rechazo=mezcla)
            codigo = C.ERROR_HARDWARE if falla.falla is Falla.ATASCO else C.TIEMPO_AGOTADO
            error = "jammed" if falla.falla is Falla.ATASCO else "timeOut"
            self._emitir("CashManagement.ItemsPurgedEvent", {"rechazo": mezcla}, id_sol)
            return Terminacion(cmd, id_sol, codigo, error, {"entregable": {}, "rechazo": mezcla})
        if falla and falla.falla is Falla.ENTREGA_PARCIAL:
            entregable, rechazo = _partir(mezcla, float(falla.parametros.get("fraccion", 0.5)))
            if rechazo:
                self._a_bin(self.rechazo, rechazo)
            self.transporte = dict(entregable)
            if not entregable:
                self.ref_transporte = None
            self._registro(ref, fase="en_transporte" if entregable else "no_presentado", rechazo=rechazo)
            self._emitir("CashManagement.ItemsPurgedEvent", {"rechazo": rechazo}, id_sol)
            return Terminacion(cmd, id_sol, C.ERROR_HARDWARE, "partialDispense", {"entregable": entregable, "rechazo": rechazo})
        return Terminacion(cmd, id_sol, C.EXITO, datos={"entregable": mezcla, "rechazo": {}})

    async def _cmd_CashDispenser_Present(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CashDispenser.Present"
        ref = str(datos["referencia"])
        if not self.transporte or self.ref_transporte != ref:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "noItems")
        m = dict(self.transporte)
        if falla and falla.falla is Falla.ATASCO:
            # Falla la persiana: los billetes nunca salen y se retraen.
            self._a_bin(self.retenido, m)
            self.transporte, self.ref_transporte = {}, None
            self._registro(ref, fase="retenido", retenido=m)
            return Terminacion(cmd, id_sol, C.ERROR_HARDWARE, "shutterJammed", {"retirado": {}, "retenido": m})
        self._registro(ref, fase="presentado")
        self._emitir("CashDispenser.ItemsPresentedEvent", {"mezcla": m}, id_sol)
        if falla and falla.falla is Falla.CORTE_ENERGIA:
            if falla.parametros.get("fase", "tras_retiro") == "tras_retiro":
                # El cliente alcanzó a tomar el efectivo y luego se fue la luz.
                self.transporte, self.ref_transporte = {}, None
                self._registro(ref, fase="retirado", entregado=m)
                self._emitir("CashManagement.ItemsTakenEvent", {"mezcla": m}, id_sol)
            self._apagar()
            raise CorteDeEnergia(f"{self.ident}: corte de energía durante {cmd}")
        if falla and falla.falla is Falla.NO_RETIRADO:
            self._a_bin(self.retenido, m)
            self.transporte, self.ref_transporte = {}, None
            self._registro(ref, fase="retenido", retenido=m)
            self._emitir("CashManagement.ItemsRetractedEvent", {"mezcla": m}, id_sol)
            return Terminacion(cmd, id_sol, C.TIEMPO_AGOTADO, "itemsNotTaken", {"retirado": {}, "retenido": m})
        self.transporte, self.ref_transporte = {}, None
        self._registro(ref, fase="retirado", entregado=m)
        self._emitir("CashManagement.ItemsTakenEvent", {"mezcla": m}, id_sol)
        return Terminacion(cmd, id_sol, C.EXITO, datos={"retirado": m, "retenido": {}})

    async def _cmd_CashDispenser_GetPresentStatus(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        reg = self.nvram.get(str(datos["referencia"]))
        info = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (reg or {"fase": "sin_operacion"}).items()}
        return Terminacion("CashDispenser.GetPresentStatus", id_sol, C.EXITO, datos=info)

    # -- recepción -----------------------------------------------------------
    async def _cmd_CashAcceptor_CashInStart(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CashAcceptor.CashInStart"
        if self.escrow:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "escrowNotEmpty")
        ref = str(datos["referencia"])
        self.ref_escrow = ref
        self._registro(ref, tipo="ingreso", fase="iniciado", billetes={})
        return Terminacion(cmd, id_sol, C.EXITO)

    async def _cmd_CashAcceptor_CashIn(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CashAcceptor.CashIn"
        ref = str(datos["referencia"])
        if self.ref_escrow != ref:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "noCashInActive")
        insertados = dinero.limpiar(datos.get("billetes", {}))
        aceptados = {d: n for d, n in insertados.items() if d in self.casetes and n > 0}
        devueltos = {d: n for d, n in insertados.items() if d not in aceptados}
        if falla and falla.falla in (Falla.ATASCO, Falla.TIEMPO_AGOTADO):
            # El validador se traba al contar: todo regresa al cliente.
            self._registro(ref, fase="devuelto")
            codigo = C.ERROR_HARDWARE if falla.falla is Falla.ATASCO else C.TIEMPO_AGOTADO
            return Terminacion(cmd, id_sol, codigo, "jammed" if falla.falla is Falla.ATASCO else "timeOut",
                               {"aceptados": {}, "devueltos": insertados})
        self.escrow = dinero.sumar(self.escrow, aceptados)
        self._registro(ref, fase="en_escrow", billetes=dict(self.escrow))
        self._emitir("CashAcceptor.InputRefuseEvent" if devueltos else "CashAcceptor.ItemsCountedEvent",
                     {"aceptados": aceptados, "devueltos": devueltos}, id_sol)
        return Terminacion(cmd, id_sol, C.EXITO, datos={"aceptados": dict(self.escrow), "devueltos": devueltos})

    async def _cmd_CashAcceptor_CashInEnd(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CashAcceptor.CashInEnd"
        ref = str(datos["referencia"])
        if self.ref_escrow != ref or not self.escrow:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "noItems")
        if falla and falla.falla is Falla.CORTE_ENERGIA:
            self._apagar()
            raise CorteDeEnergia(f"{self.ident}: corte de energía durante {cmd}")
        if falla and falla.falla in (Falla.ATASCO, Falla.TIEMPO_AGOTADO):
            codigo = C.ERROR_HARDWARE if falla.falla is Falla.ATASCO else C.TIEMPO_AGOTADO
            return Terminacion(cmd, id_sol, codigo, "jammed" if falla.falla is Falla.ATASCO else "timeOut",
                               {"escrow": dict(self.escrow)})
        a_casete: Mezcla = {}
        a_excedente: Mezcla = {}
        for d, n in self.escrow.items():
            c = self.casetes[d]
            cabe = max(0, min(n, c.capacidad - c.cantidad))
            c.cantidad += cabe
            if cabe:
                a_casete[d] = cabe
            if n - cabe:
                a_excedente[d] = n - cabe
        self._a_bin(self.excedente, a_excedente)
        almacenado = dict(self.escrow)
        self.escrow, self.ref_escrow = {}, None
        self._registro(ref, fase="almacenado", destinos={"casete": a_casete, "excedente": a_excedente})
        return Terminacion(cmd, id_sol, C.EXITO,
                           datos={"almacenado": almacenado, "destinos": {"casete": a_casete, "excedente": a_excedente}})

    async def _cmd_CashAcceptor_CashInRollback(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        cmd = "CashAcceptor.CashInRollback"
        ref = str(datos["referencia"])
        if self.ref_escrow != ref:
            return Terminacion(cmd, id_sol, C.ERROR_COMANDO, "noCashInActive")
        m = dict(self.escrow)
        if falla and falla.falla is Falla.ATASCO:
            self._a_bin(self.retenido, m)
            self.escrow, self.ref_escrow = {}, None
            self._registro(ref, fase="retenido", retenido=m)
            return Terminacion(cmd, id_sol, C.ERROR_HARDWARE, "retained", {"devuelto": {}, "retenido": m})
        self.escrow, self.ref_escrow = {}, None
        self._registro(ref, fase="devuelto")
        return Terminacion(cmd, id_sol, C.EXITO, datos={"devuelto": m, "retenido": {}})

    async def _cmd_CashAcceptor_GetCashInStatus(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        reg = self.nvram.get(str(datos["referencia"]))
        info = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (reg or {"fase": "sin_operacion"}).items()}
        return Terminacion("CashAcceptor.GetCashInStatus", id_sol, C.EXITO, datos=info)

    # -- administración --------------------------------------------------------
    async def _cmd_CashManagement_GetCashUnitInfo(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        return Terminacion("CashManagement.GetCashUnitInfo", id_sol, C.EXITO, datos=self.inventario())

    async def _cmd_Storage_Replenish(self, id_sol: int, datos: dict, falla: FallaProgramada | None) -> Terminacion:
        mezcla = dinero.limpiar(datos["mezcla"])
        for d, n in mezcla.items():
            c = self.casetes.get(d)
            if c is None or c.cantidad + n > c.capacidad:
                return Terminacion("Storage.Replenish", id_sol, C.ERROR_COMANDO, "capacityExceeded")
        for d, n in mezcla.items():
            self.casetes[d].cantidad += n
        return Terminacion("Storage.Replenish", id_sol, C.EXITO, datos={"cargado": mezcla})


def _partir(mezcla: Mezcla, fraccion: float) -> tuple[Mezcla, Mezcla]:
    """Separa una mezcla en (entregable, rechazo) para una entrega parcial.

    Se entregan los primeros ``fraccion`` billetes (de mayor a menor
    denominación); el resto se purga al casete de rechazo. Con un solo billete
    no hay parcial posible: todo va a rechazo.
    """
    fila = [d for d in sorted(mezcla, reverse=True) for _ in range(mezcla[d])]
    if len(fila) <= 1:
        return {}, dict(mezcla)
    k = min(len(fila) - 1, max(1, int(len(fila) * fraccion)))
    entregable: Mezcla = {}
    rechazo: Mezcla = {}
    for i, d in enumerate(fila):
        destino = entregable if i < k else rechazo
        destino[d] = destino.get(d, 0) + 1
    return entregable, rechazo
