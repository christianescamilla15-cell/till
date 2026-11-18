"""Till: implementación de referencia de una red de cajeros de sucursal automatizados.

Etapa 1 (este paquete): automatización determinista. Todo lo físico es simulado.

Módulos:
    devices  dispositivos simulados (reciclador, lector EMV, impresora, cámara)
    host     enlace con el banco en ISO 8583 y autorizador simulado
    ledger   libro mayor de partida doble, sólo-anexar, con llaves de idempotencia
    core     máquina de estados por transacción, bitácora y reversas
    recon    cuadre de fin de día
    fleet    simulador de flota
    api      API HTTP (FastAPI)
"""

__version__ = "0.1.0"
