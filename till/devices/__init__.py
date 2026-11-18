"""Dispositivos simulados con un modelo de comandos y eventos inspirado en XFS4IoT."""

from till.devices.base import (
    CodigoTerminacion,
    CorteDeEnergia,
    Dispositivo,
    Evento,
    Falla,
    FallaNoSoportada,
    Terminacion,
)
from till.devices.camara import Camara
from till.devices.efectivo import RecicladorEfectivo
from till.devices.impresora import ImpresoraComprobantes
from till.devices.tarjeta import LectorTarjetas, PanNoPermitido, SesionTarjeta

__all__ = [
    "Camara",
    "CodigoTerminacion",
    "CorteDeEnergia",
    "Dispositivo",
    "Evento",
    "Falla",
    "FallaNoSoportada",
    "ImpresoraComprobantes",
    "LectorTarjetas",
    "PanNoPermitido",
    "RecicladorEfectivo",
    "SesionTarjeta",
    "Terminacion",
]
