"""Arma una red completa: host simulado, enlace, bitácora, libro mayor y cajeros.

Lo usan la API, la muestra de alta fidelidad del simulador de flota y las
pruebas. ``reabrir`` simula reiniciar el proceso: cierra la base, la vuelve a
abrir y crea cajeros nuevos sobre el mismo hardware (que no se reinicia con el
proceso) y el mismo banco.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from till import dinero
from till.core import Bitacora, Cajero, ReenvioReversas
from till.db import BaseDatos
from till.devices import Camara, ImpresoraComprobantes, LectorTarjetas, RecicladorEfectivo
from till.dinero import Mezcla
from till.host import ClienteHost, DatosTerminal, HostAutorizador, TransporteEnProceso
from till.ledger import LibroMayor, cuentas
from till.seguridad import Tokenizador

# Carga inicial de referencia por cajero (billetes por denominación): $397,000.
CARGA_ESTANDAR: Mezcla = {20: 100, 50: 100, 100: 300, 200: 300, 500: 400, 1000: 100}

# Tarjetas y cuentas de prueba (sólo PANs de prueba públicos).
TARJETAS_DEMO: list[tuple[str, str, int]] = [
    ("4111111111111111", "CTA-0001", 50_000),
    ("5555555555554444", "CTA-0002", 1_500),
    ("4012888888881881", "CTA-0003", 250_000),
    ("5105105105105100", "CTA-0004", 10_000),
]
CREDITOS_DEMO: dict[str, int] = {"CRED-0001": 12_000}


def terminal_id(i: int) -> str:
    return f"TILL{i:04d}"


@dataclass
class Hardware:
    reciclador: RecicladorEfectivo
    lector: LectorTarjetas
    impresora: ImpresoraComprobantes
    camara: Camara


@dataclass
class Red:
    ruta_db: str
    db: BaseDatos
    host: HostAutorizador
    transporte: TransporteEnProceso
    cliente: ClienteHost
    bitacora: Bitacora
    libro: LibroMayor
    reversas: ReenvioReversas
    tokenizador: Tokenizador
    hardware: dict[str, Hardware] = field(default_factory=dict)
    cajeros: dict[str, Cajero] = field(default_factory=dict)

    def cajero(self, terminal: str) -> Cajero:
        return self.cajeros[terminal]

    def _crear_cajero(self, terminal: str) -> Cajero:
        hw = self.hardware[terminal]
        dt = DatosTerminal(terminal, f"SUC{terminal}".ljust(15, "0"), f"SUCURSAL SIMULADA {terminal}")
        c = Cajero(dt, self.db, self.bitacora, self.libro, self.cliente, self.reversas,
                   hw.reciclador, hw.lector, hw.impresora, hw.camara)
        self.cajeros[terminal] = c
        return c

    def agregar_cajero(self, terminal: str, carga: Mezcla | None = None, latencia: float = 0.0) -> Cajero:
        carga = dict(carga or CARGA_ESTANDAR)
        self.hardware[terminal] = Hardware(
            RecicladorEfectivo(f"{terminal}-EFECTIVO", carga, latencia=latencia),
            LectorTarjetas(f"{terminal}-TARJETA", self.tokenizador, latencia=latencia),
            ImpresoraComprobantes(f"{terminal}-IMPRESORA", latencia=latencia),
            Camara(f"{terminal}-CAMARA", latencia=latencia),
        )
        # La carga inicial entra al libro como reabasto desde la bóveda.
        self.libro.registrar(f"reabasto:{terminal}:inicial", "reabasto", cuentas.reabasto(terminal, carga), terminal)
        return self._crear_cajero(terminal)

    def cerrar(self) -> None:
        self.db.cerrar()


def construir_red(
    ruta_db: str | Path,
    cajeros: int = 3,
    carga: Mezcla | None = None,
    timeout_host: float = 2.0,
    latencia: float = 0.0,
    sincrono: str = "FULL",
) -> Red:
    tokenizador = Tokenizador()
    host = HostAutorizador(tokenizador)
    for pan, cuenta, saldo in TARJETAS_DEMO:
        host.alta_cuenta(cuenta, dinero.pesos(saldo), limite_retiro_diario=dinero.pesos(60_000))
        host.alta_tarjeta(pan, cuenta)
    for credito, adeudo in CREDITOS_DEMO.items():
        host.alta_credito(credito, dinero.pesos(adeudo))
    transporte = TransporteEnProceso(host, latencia=latencia)
    red = _armar(str(ruta_db), host, transporte, tokenizador, timeout_host, sincrono)
    for i in range(1, cajeros + 1):
        red.agregar_cajero(terminal_id(i), carga, latencia)
    return red


def _armar(ruta_db: str, host: HostAutorizador, transporte: TransporteEnProceso, tokenizador: Tokenizador,
           timeout_host: float, sincrono: str = "FULL") -> Red:
    db = BaseDatos(ruta_db, sincrono)
    bitacora = Bitacora(db)
    libro = LibroMayor(db)
    cliente = ClienteHost(transporte, timeout=timeout_host)
    reversas = ReenvioReversas(db, bitacora, libro, cliente)
    return Red(ruta_db, db, host, transporte, cliente, bitacora, libro, reversas, tokenizador)


def reabrir(red: Red) -> Red:
    """Simula el reinicio del proceso: misma base en disco, mismo hardware, mismo banco."""
    red.db.cerrar()
    nueva = _armar(red.ruta_db, red.host, red.transporte, red.tokenizador, red.cliente.timeout)
    nueva.hardware = red.hardware
    for terminal in red.hardware:
        nueva._crear_cajero(terminal)
    return nueva
