#!/usr/bin/env python3
"""Modelo ArchiMate 3.1 de Till: una sola fuente para el XML de intercambio y su vista legible.

    python3 herramientas/archimate.py            # escribe docs/arquitectura/till.archimate.xml y archimate.md
    python3 herramientas/archimate.py --revisar  # sale con 1 si lo versionado no coincide con este modelo

El XML sigue el formato de intercambio de The Open Group (ArchiMate 3.1 Model Exchange File Format) y se valida
contra su XSD oficial con `herramientas/validar_archimate.sh` (xmllint). Cada componente de aplicación y cada
objeto de datos lleva la propiedad «código» con el archivo real que lo implementa: tests/test_archimate.py
exige que exista, que no haya identificadores repetidos ni relaciones colgadas y que cada relación sea de un
tipo que ArchiMate permite entre esos dos elementos. Nada aquí se dibuja a mano.
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
XML = RAIZ / "docs" / "arquitectura" / "till.archimate.xml"
MD = RAIZ / "docs" / "arquitectura" / "archimate.md"

# (identificador, tipo ArchiMate, nombre, documentación, archivo del repo que lo implementa o lo describe)
ELEMENTOS: list[tuple[str, str, str, str, str | None]] = [
    # -- motivación ----------------------------------------------------------------------------------------
    ("st-cliente", "Stakeholder", "Cliente de sucursal", "Quien retira, deposita, paga su crédito o envía dinero.", None),
    ("st-banco", "Stakeholder", "Banco (emisor y adquirente)", "Dueño de las cuentas y de la red de cajeros.", None),
    ("st-operacion", "Stakeholder", "Operación de la red", "Soporte técnico, monitoreo y traslado de valores.", "docs/etapas.md"),
    ("st-cumplimiento", "Stakeholder", "Riesgo, cumplimiento y auditoría", "PCI DSS, auditoría interna y regulación.", None),
    ("dr-sustitucion", "Driver", "Sustituir 15 000 cajeros humanos de sucursal por máquinas",
     "El motivo de Till (README, «Por qué existe»).", "README.md"),
    ("dr-pci", "Driver", "Datos de tarjeta bajo PCI DSS", "El PAN y el PIN tienen reglas de manejo obligatorias.", "till/seguridad.py"),
    ("dr-confianza", "Driver", "Confianza del cliente en el efectivo", "El cliente tiene que confiar en la máquina como en la ventanilla.", None),
    ("as-cargo", "Assessment", "Un cargo sin entrega (o una entrega sin cargo) rompe la confianza",
     "Es el modo de falla que más aclaraciones y quejas genera en una red de cajeros.", None),
    ("go-exactitud", "Goal", "Nunca cargo sin entrega ni entrega sin cargo", "Principio rector del README.", "README.md"),
    ("go-cuadre", "Goal", "Cuadre diario exacto a tres bandas", "Físico = libro mayor = banco, todos los días.", "till/recon/cuadre.py"),
    ("go-disponibilidad", "Goal", "Disponibilidad medible por cajero a escala de 15 000", "Metas por oleada en docs/etapas.md.", "docs/etapas.md"),
    ("go-pan", "Goal", "Ningún PAN en claro fuera de la sesión de tarjeta", "Ni en disco, ni en trazas, ni en comprobantes.", "till/seguridad.py"),
    ("pr-rector", "Principle", "Una operación no cuenta hasta que el dispositivo confirma y el banco acusa recibo",
     "De aquí salen la máquina de estados, las reversas y la recuperación.", "till/core/maquina.py"),
    ("pr-una-vez", "Principle", "Exactamente una vez", "Llave por solicitud, llave por asiento y un banco idempotente.", "till/core/bitacora.py"),
    ("pr-anexar", "Principle", "Sólo anexar", "Una corrección es otro asiento; el historial no se reescribe.", "till/ledger/libro.py"),
    ("pr-ia", "Principle", "La IA propone, las reglas autorizan", "Etapa 2: ningún modelo mueve dinero.", "docs/etapas.md"),
    ("rq-reversa", "Requirement", "RF-03 · Toda falla tras autorizar se revierte por lo no entregado",
     "Total o parcial con campo 95; reglas R1–R9.", "docs/reglas.md"),
    ("rq-recuperacion", "Requirement", "RNF-03 · Recuperar tras un corte sin perder ni duplicar", "Reglas E8, I7, F4.", "docs/reglas.md"),
    ("rq-idempotencia", "Requirement", "RF-05 · Idempotencia de la solicitud", "Reglas I1–I3.", "docs/reglas.md"),
    ("rq-auditoria", "Requirement", "RNF-06 · Rastro de auditoría sólo-anexar", "Reglas E3, L2.", "docs/reglas.md"),
    ("rq-pan", "Requirement", "RNF-05 · Tokenizar el PAN en el borde", "Reglas P1–P5.", "docs/reglas.md"),
    ("rq-cuadre", "Requirement", "RF-06 · Cuadre físico = libro = banco", "Reglas C1–C7.", "docs/reglas.md"),
    ("co-pan-prueba", "Constraint", "Sólo PANs de prueba públicos", "Etapa 1: todo lo físico y el banco son simulados.", "till/seguridad.py"),
    ("co-estatica", "Constraint", "La demo pública es un sitio estático sin servidor", "ADR 0002.", "docs/adr/0002-api-en-el-navegador-con-pyodide.md"),
    # -- negocio -------------------------------------------------------------------------------------------
    ("ba-cliente", "BusinessActor", "Cliente", "", None),
    ("ba-banco", "BusinessActor", "Banco", "Autoriza (0210) y acusa reversas (0430).", "till/host/autorizador.py"),
    ("ba-operador", "BusinessActor", "Operador de la red", "", None),
    ("br-tarjetahabiente", "BusinessRole", "Tarjetahabiente", "", None),
    ("bs-retiro", "BusinessService", "Retiro de efectivo", "", "till/core/maquina.py"),
    ("bs-deposito", "BusinessService", "Depósito en efectivo", "", "till/core/maquina.py"),
    ("bs-pago", "BusinessService", "Pago de crédito en efectivo", "", "till/core/maquina.py"),
    ("bs-remesa", "BusinessService", "Envío de remesa en efectivo", "Con clave de cobro.", "till/core/maquina.py"),
    ("bs-cuadre", "BusinessService", "Cuadre de fin de día", "", "till/recon/cuadre.py"),
    ("bs-flota", "BusinessService", "Planeación de la flota y del reabasto", "", "till/fleet/simulador.py"),
    ("bp-operacion", "BusinessProcess", "Operación en el cajero: autorizar → entregar o recibir → confirmar", "", "till/core/maquina.py"),
    ("bp-reversa", "BusinessProcess", "Reversa: almacenar y reenviar hasta el acuse", "", "till/core/reversas.py"),
    ("bp-recuperacion", "BusinessProcess", "Recuperación tras un corte de energía", "", "till/core/maquina.py"),
    ("bp-cuadre", "BusinessProcess", "Cuadre a tres bandas", "", "till/recon/cuadre.py"),
    ("bo-operacion", "BusinessObject", "Operación", "", None),
    ("bo-asiento", "BusinessObject", "Asiento contable", "", None),
    ("bo-reversa", "BusinessObject", "Reversa", "", None),
    ("bo-comprobante", "BusinessObject", "Comprobante", "", None),
    ("bo-aclaracion", "BusinessObject", "Aclaración (efectivo retenido)", "", None),
    # -- aplicación (el código) ------------------------------------------------------------------------------
    ("ac-maquina", "ApplicationComponent", "Máquina de estados del cajero", "AUTORIZAR → ENTREGAR/RECIBIR → CONFIRMAR, recuperación.",
     "till/core/maquina.py"),
    ("ac-bitacora", "ApplicationComponent", "Bitácora transaccional", "Estados, historial, STAN y cola de reversas (escritura anticipada).",
     "till/core/bitacora.py"),
    ("ac-reversas", "ApplicationComponent", "Reenvío de reversas", "0420/0421 con espera exponencial hasta el 0430.", "till/core/reversas.py"),
    ("ac-libro", "ApplicationComponent", "Libro mayor de partida doble", "Sólo anexar, con llave de idempotencia por asiento.", "till/ledger/libro.py"),
    ("ac-cuadre", "ApplicationComponent", "Cuadre a tres bandas", "Físico contra libro, libro contra banco.", "till/recon/cuadre.py"),
    ("ac-dispositivos", "ApplicationComponent", "Dispositivos simulados", "Reciclador, lector EMV, impresora y cámara con inyección de fallas.",
     "till/devices/base.py"),
    ("ac-cliente-iso", "ApplicationComponent", "Cliente ISO 8583", "Arma 0200, 0420/0421 y 0800 del lado del cajero.", "till/host/cliente.py"),
    ("ac-enlace", "ApplicationComponent", "Enlace con el banco", "Transporte en proceso (con fallas) o TCP con marco de 2 bytes.",
     "till/host/enlace.py"),
    ("ac-autorizador", "ApplicationComponent", "Autorizador simulado (banco)", "Cuentas, límites, créditos, remesas; idempotente.",
     "till/host/autorizador.py"),
    ("ac-traza", "ApplicationComponent", "Traza ISO 8583", "Lo que viajó, sin PAN en claro ni campo 55.", "till/host/traza.py"),
    ("ac-seguridad", "ApplicationComponent", "Tokenización y criptograma", "Token con llave, enmascarado 6+4, ARQC simulado.", "till/seguridad.py"),
    ("ac-red", "ApplicationComponent", "Armado de la red", "Banco, enlace, bitácora, libro y cajeros en una sola base.", "till/red.py"),
    ("ac-flota", "ApplicationComponent", "Simulador de flota", "Modelo agregado de hasta 15 000 cajeros y muestra de alta fidelidad.",
     "till/fleet/simulador.py"),
    ("ac-api", "ApplicationComponent", "API HTTP (FastAPI)", "La misma app en local, en las pruebas y en el navegador.", "till/api/app.py"),
    ("ac-sesiones", "ApplicationComponent", "Sesiones de la demo", "Una red por visitante (X-Till-Sesion).", "till/api/sesiones.py"),
    ("ac-web", "ApplicationComponent", "Demo web (React)", "El cajero, el taller de fallas, los libros y la flota.", "web/src/App.jsx"),
    ("ac-motor", "ApplicationComponent", "Motor en el navegador", "Web worker con Pyodide y el puente ASGI hacia la app.",
     "web/src/motor/motor.worker.js"),
    ("ai-rest", "ApplicationInterface", "API REST /api (OpenAPI)", "Contrato versionado.", "docs/api/openapi.json"),
    ("ai-iso", "ApplicationInterface", "Mensajes ISO 8583", "0200/0210, 0420/0421/0430, 0800/0810.", "till/host/iso.py"),
    ("ai-dispositivos", "ApplicationInterface", "Comandos y eventos de dispositivo (estilo XFS4IoT)", "", "till/devices/base.py"),
    ("as-autorizar", "ApplicationService", "Autorizar operación", "", "till/core/maquina.py"),
    ("as-revertir", "ApplicationService", "Revertir operación", "", "till/core/reversas.py"),
    ("as-registrar", "ApplicationService", "Registrar asiento", "", "till/ledger/libro.py"),
    ("as-cuadrar", "ApplicationService", "Cuadrar terminal", "", "till/recon/cuadre.py"),
    ("as-simular", "ApplicationService", "Simular flota", "", "till/fleet/simulador.py"),
    ("do-transacciones", "DataObject", "Transacciones e historial", "Tablas transacciones e historial.", "till/core/bitacora.py"),
    ("do-reversas", "DataObject", "Cola de reversas", "Tabla reversas.", "till/core/bitacora.py"),
    ("do-asientos", "DataObject", "Asientos y movimientos", "Tablas asientos y movimientos.", "till/ledger/libro.py"),
    ("do-mensaje", "DataObject", "Mensaje ISO 8583", "", "till/host/iso.py"),
    ("do-token", "DataObject", "Token de tarjeta y PAN enmascarado", "", "till/seguridad.py"),
    # -- tecnología ------------------------------------------------------------------------------------------
    ("nd-navegador", "Node", "Navegador del visitante", "", None),
    ("nd-vercel", "Node", "Vercel (sitio estático en CDN)", "", "vercel.json"),
    ("nd-ci", "Node", "GitHub Actions", "CI y escaneos.", ".github/workflows/ci.yml"),
    ("nd-edge", "Node", "PC industrial del cajero (diseño de referencia)", "Etapa 1: simulado.", "docs/hardware.md"),
    ("ss-pyodide", "SystemSoftware", "Pyodide (CPython 3.14 en WebAssembly)", "", "web/scripts/preparar_motor.mjs"),
    ("ss-worker", "SystemSoftware", "Web Worker del navegador", "", "web/src/motor/motor.worker.js"),
    ("ss-sqlite", "SystemSoftware", "SQLite (WAL, synchronous=FULL)", "", "till/db.py"),
    ("ss-cpython", "SystemSoftware", "CPython 3.12 + uvicorn", "Local y CI.", "api/index.py"),
    ("dv-reciclador", "Device", "Reciclador de efectivo", "", "docs/hardware.md"),
    ("dv-lector", "Device", "Lector EMV", "", "docs/hardware.md"),
    ("dv-impresora", "Device", "Impresora térmica", "", "docs/hardware.md"),
    ("dv-camara", "Device", "Cámara", "", "docs/hardware.md"),
    ("ts-hosting", "TechnologyService", "Hosting estático con CSP estricta", "", "vercel.json"),
    ("ts-python", "TechnologyService", "Python en el navegador", "", "web/scripts/preparar_motor.mjs"),
    ("ts-persistencia", "TechnologyService", "Persistencia transaccional local", "Bitácora y libro en una transacción.", "till/db.py"),
    ("ar-dist", "Artifact", "web/dist (sitio publicado)", "", "web/vite.config.js"),
    ("ar-motor", "Artifact", "motor/till.json + pyodide-lock.json recortado", "", "web/scripts/preparar_motor.mjs"),
    ("ar-vercel", "Artifact", "vercel.json", "", "vercel.json"),
    ("pa-enlace", "Path", "Enlace ISO 8583 sobre TCP (marco de 2 bytes)", "", "till/host/enlace.py"),
    # -- implementación y migración ---------------------------------------------------------------------------
    ("pl-base", "Plateau", "Línea base: API como función de Python en Vercel", "Descartada: estado repartido entre instancias (ADR 0002).",
     "docs/adr/0002-api-en-el-navegador-con-pyodide.md"),
    ("pl-objetivo", "Plateau", "Objetivo Etapa 1: API en el navegador, CI y calidad de referencia", "", "docs/arquitectura/vision.md"),
    ("pl-etapa2", "Plateau", "Etapa 2: IA detrás de una compuerta de reglas", "Sólo diseñada.", "docs/etapas.md"),
    ("gp-estado", "Gap", "Estado en memoria repartido entre instancias sin servidor", "", "docs/adr/0002-api-en-el-navegador-con-pyodide.md"),
    ("wp-calidad", "WorkPackage", "Programa de calidad: CI, reglas → pruebas, motor probado", "", "docs/reglas.md"),
    ("wp-piloto", "WorkPackage", "Laboratorio y piloto de 50 sucursales", "", "docs/etapas.md"),
]

# (identificador, tipo, origen, destino, nombre)
RELACIONES: list[tuple[str, str, str, str, str]] = [
    # motivación
    ("r01", "Association", "st-cliente", "dr-confianza", ""),
    ("r02", "Association", "st-banco", "dr-sustitucion", ""),
    ("r03", "Association", "st-operacion", "dr-sustitucion", ""),
    ("r04", "Association", "st-cumplimiento", "dr-pci", ""),
    ("r05", "Influence", "dr-confianza", "as-cargo", ""),
    ("r06", "Influence", "as-cargo", "go-exactitud", ""),
    ("r07", "Influence", "dr-sustitucion", "go-disponibilidad", ""),
    ("r08", "Influence", "dr-pci", "go-pan", ""),
    ("r09", "Influence", "dr-confianza", "go-cuadre", ""),
    ("r10", "Realization", "pr-rector", "go-exactitud", ""),
    ("r11", "Realization", "pr-una-vez", "go-exactitud", ""),
    ("r12", "Realization", "pr-anexar", "go-cuadre", ""),
    ("r13", "Realization", "rq-reversa", "pr-rector", ""),
    ("r14", "Realization", "rq-recuperacion", "pr-rector", ""),
    ("r15", "Realization", "rq-idempotencia", "pr-una-vez", ""),
    ("r16", "Realization", "rq-auditoria", "pr-anexar", ""),
    ("r17", "Realization", "rq-pan", "go-pan", ""),
    ("r18", "Realization", "rq-cuadre", "go-cuadre", ""),
    ("r19", "Influence", "co-pan-prueba", "rq-pan", ""),
    # motivación ← aplicación
    ("r20", "Realization", "ac-reversas", "rq-reversa", ""),
    ("r21", "Realization", "ac-maquina", "rq-recuperacion", ""),
    ("r22", "Realization", "ac-bitacora", "rq-idempotencia", ""),
    ("r23", "Realization", "ac-libro", "rq-auditoria", ""),
    ("r24", "Realization", "ac-seguridad", "rq-pan", ""),
    ("r25", "Realization", "ac-cuadre", "rq-cuadre", ""),
    ("r26", "Realization", "ac-motor", "co-estatica", ""),
    # negocio
    ("r30", "Assignment", "ba-cliente", "br-tarjetahabiente", ""),
    ("r31", "Serving", "bs-retiro", "br-tarjetahabiente", ""),
    ("r32", "Serving", "bs-deposito", "br-tarjetahabiente", ""),
    ("r33", "Serving", "bs-pago", "br-tarjetahabiente", ""),
    ("r34", "Serving", "bs-remesa", "br-tarjetahabiente", ""),
    ("r35", "Serving", "bs-cuadre", "ba-operador", ""),
    ("r36", "Serving", "bs-flota", "ba-operador", ""),
    ("r37", "Realization", "bp-operacion", "bs-retiro", ""),
    ("r38", "Realization", "bp-operacion", "bs-deposito", ""),
    ("r39", "Realization", "bp-operacion", "bs-pago", ""),
    ("r40", "Realization", "bp-operacion", "bs-remesa", ""),
    ("r41", "Realization", "bp-cuadre", "bs-cuadre", ""),
    ("r42", "Triggering", "bp-operacion", "bp-reversa", "falla tras autorizar"),
    ("r43", "Triggering", "bp-recuperacion", "bp-reversa", "sin prueba de entrega"),
    ("r44", "Access", "bp-operacion", "bo-operacion", ""),
    ("r45", "Access", "bp-operacion", "bo-comprobante", ""),
    ("r46", "Access", "bp-operacion", "bo-aclaracion", ""),
    ("r47", "Access", "bp-operacion", "bo-asiento", ""),
    ("r48", "Access", "bp-reversa", "bo-reversa", ""),
    ("r49", "Access", "bp-cuadre", "bo-asiento", ""),
    ("r50", "Association", "ba-banco", "bp-operacion", "autoriza"),
    ("r51", "Association", "ba-banco", "bp-reversa", "acusa"),
    # negocio ← aplicación
    ("r60", "Serving", "as-autorizar", "bp-operacion", ""),
    ("r61", "Serving", "as-registrar", "bp-operacion", ""),
    ("r62", "Serving", "as-revertir", "bp-reversa", ""),
    ("r63", "Serving", "as-cuadrar", "bp-cuadre", ""),
    ("r64", "Serving", "as-simular", "ba-operador", ""),
    ("r65", "Realization", "ac-maquina", "as-autorizar", ""),
    ("r66", "Realization", "ac-reversas", "as-revertir", ""),
    ("r67", "Realization", "ac-libro", "as-registrar", ""),
    ("r68", "Realization", "ac-cuadre", "as-cuadrar", ""),
    ("r69", "Realization", "ac-flota", "as-simular", ""),
    ("r70", "Realization", "do-transacciones", "bo-operacion", ""),
    ("r71", "Realization", "do-reversas", "bo-reversa", ""),
    ("r72", "Realization", "do-asientos", "bo-asiento", ""),
    # aplicación
    ("r80", "Composition", "ac-api", "ai-rest", ""),
    ("r81", "Composition", "ac-cliente-iso", "ai-iso", ""),
    ("r82", "Composition", "ac-dispositivos", "ai-dispositivos", ""),
    ("r83", "Composition", "ac-api", "ac-sesiones", ""),
    ("r84", "Serving", "ac-bitacora", "ac-maquina", ""),
    ("r85", "Serving", "ac-reversas", "ac-maquina", ""),
    ("r86", "Serving", "ac-libro", "ac-maquina", ""),
    ("r87", "Serving", "ac-cliente-iso", "ac-maquina", ""),
    ("r88", "Serving", "ai-dispositivos", "ac-maquina", ""),
    ("r89", "Serving", "ac-enlace", "ac-cliente-iso", ""),
    ("r90", "Serving", "ac-traza", "ac-cliente-iso", ""),
    ("r91", "Serving", "ac-seguridad", "ac-dispositivos", ""),
    ("r92", "Serving", "ac-maquina", "ac-cuadre", ""),
    ("r93", "Serving", "ac-autorizador", "ac-cuadre", "totales del banco"),
    ("r94", "Serving", "ac-red", "ac-api", ""),
    ("r95", "Serving", "ac-cuadre", "ac-api", ""),
    ("r96", "Serving", "ac-flota", "ac-api", ""),
    ("r97", "Serving", "ac-api", "ac-motor", "llamada ASGI"),
    ("r98", "Serving", "ac-motor", "ac-web", "postMessage"),
    ("r99", "Serving", "ai-rest", "ac-web", ""),
    ("r100", "Flow", "ac-enlace", "ac-autorizador", "0200 · 0420/0421"),
    ("r101", "Serving", "ac-reversas", "ac-cliente-iso", ""),
    ("r102", "Access", "ac-bitacora", "do-transacciones", ""),
    ("r103", "Access", "ac-bitacora", "do-reversas", ""),
    ("r104", "Access", "ac-reversas", "do-reversas", ""),
    ("r105", "Access", "ac-libro", "do-asientos", ""),
    ("r106", "Access", "ac-cuadre", "do-asientos", ""),
    ("r107", "Access", "ac-cliente-iso", "do-mensaje", ""),
    ("r108", "Access", "ac-traza", "do-mensaje", ""),
    ("r109", "Access", "ac-seguridad", "do-token", ""),
    # tecnología
    ("r120", "Composition", "nd-navegador", "ss-worker", ""),
    ("r121", "Composition", "nd-navegador", "ss-pyodide", ""),
    ("r122", "Composition", "nd-ci", "ss-cpython", ""),
    ("r123", "Aggregation", "nd-edge", "dv-reciclador", ""),
    ("r124", "Aggregation", "nd-edge", "dv-lector", ""),
    ("r125", "Aggregation", "nd-edge", "dv-impresora", ""),
    ("r126", "Aggregation", "nd-edge", "dv-camara", ""),
    ("r127", "Assignment", "nd-vercel", "ar-dist", ""),
    ("r128", "Assignment", "nd-vercel", "ar-vercel", ""),
    ("r129", "Assignment", "ss-pyodide", "ar-motor", ""),
    ("r130", "Realization", "ar-dist", "ac-web", ""),
    ("r131", "Realization", "ar-motor", "ac-motor", ""),
    ("r132", "Realization", "ar-motor", "ac-api", ""),
    ("r133", "Realization", "nd-vercel", "ts-hosting", ""),
    ("r134", "Realization", "ss-pyodide", "ts-python", ""),
    ("r135", "Realization", "ss-sqlite", "ts-persistencia", ""),
    ("r136", "Serving", "ts-hosting", "ac-web", ""),
    ("r137", "Serving", "ts-python", "ac-motor", ""),
    ("r138", "Serving", "ts-persistencia", "ac-bitacora", ""),
    ("r139", "Serving", "ts-persistencia", "ac-libro", ""),
    ("r140", "Association", "dv-reciclador", "ac-dispositivos", "simulado por"),
    ("r141", "Association", "dv-lector", "ac-dispositivos", "simulado por"),
    ("r142", "Association", "dv-impresora", "ac-dispositivos", "simulado por"),
    ("r143", "Association", "dv-camara", "ac-dispositivos", "simulado por"),
    ("r144", "Association", "pa-enlace", "nd-edge", ""),
    ("r145", "Association", "pa-enlace", "ac-enlace", "implementado por"),
    ("r146", "Composition", "nd-navegador", "ss-sqlite", "en memoria del worker"),
    # implementación y migración
    ("r150", "Triggering", "pl-base", "pl-objetivo", ""),
    ("r151", "Triggering", "pl-objetivo", "pl-etapa2", ""),
    ("r152", "Association", "gp-estado", "pl-base", ""),
    ("r153", "Association", "gp-estado", "pl-objetivo", ""),
    ("r154", "Association", "wp-calidad", "pl-objetivo", ""),
    ("r155", "Association", "wp-piloto", "pl-etapa2", ""),
    ("r156", "Aggregation", "pl-objetivo", "ac-motor", ""),
    ("r157", "Association", "pr-ia", "pl-etapa2", ""),
]

# (identificador, nombre, explicación, filas de elementos: cada fila se dibuja de izquierda a derecha)
VISTAS: list[tuple[str, str, str, list[list[str]]]] = [
    ("v-motivacion", "Motivación",
     "Por qué existe Till y qué principios lo gobiernan. Los requisitos citan las reglas de docs/reglas.md.",
     [["st-cliente", "st-banco", "st-operacion", "st-cumplimiento"],
      ["dr-confianza", "dr-sustitucion", "dr-pci", "as-cargo"],
      ["go-exactitud", "go-cuadre", "go-disponibilidad", "go-pan"],
      ["pr-rector", "pr-una-vez", "pr-anexar", "pr-ia"],
      ["rq-reversa", "rq-recuperacion", "rq-idempotencia", "rq-auditoria"],
      ["rq-pan", "rq-cuadre", "co-pan-prueba", "co-estatica"]]),
    ("v-negocio", "Negocio",
     "Los servicios de ventanilla que el cajero automatiza y los procesos que los realizan.",
     [["ba-cliente", "br-tarjetahabiente", "ba-banco", "ba-operador"],
      ["bs-retiro", "bs-deposito", "bs-pago", "bs-remesa"],
      ["bs-cuadre", "bs-flota", "bp-operacion", "bp-reversa"],
      ["bp-recuperacion", "bp-cuadre", "bo-operacion", "bo-asiento"],
      ["bo-reversa", "bo-comprobante", "bo-aclaracion"]]),
    ("v-aplicacion", "Aplicación",
     "Los componentes reales del código (propiedad «código») y los servicios que dan al negocio.",
     [["ac-web", "ac-motor", "ai-rest", "ac-api"],
      ["ac-sesiones", "ac-red", "ac-flota", "ac-cuadre"],
      ["ac-maquina", "ac-bitacora", "ac-reversas", "ac-libro"],
      ["ac-dispositivos", "ai-dispositivos", "ac-seguridad", "ac-cliente-iso"],
      ["ai-iso", "ac-enlace", "ac-traza", "ac-autorizador"],
      ["as-autorizar", "as-revertir", "as-registrar", "as-cuadrar"],
      ["as-simular", "do-transacciones", "do-reversas", "do-asientos"],
      ["do-mensaje", "do-token"]]),
    ("v-tecnologia", "Tecnología",
     "Dónde corre: la demo publicada es estática y la API corre en el navegador; el cajero de referencia, en el borde.",
     [["nd-vercel", "ar-dist", "ar-vercel", "ts-hosting"],
      ["nd-navegador", "ss-worker", "ss-pyodide", "ar-motor"],
      ["ts-python", "ss-sqlite", "ts-persistencia", "nd-ci"],
      ["ss-cpython", "nd-edge", "pa-enlace", "dv-reciclador"],
      ["dv-lector", "dv-impresora", "dv-camara", "ac-web"],
      ["ac-motor", "ac-api", "ac-bitacora", "ac-libro"],
      ["ac-dispositivos", "ac-enlace"]]),
    ("v-migracion", "Implementación y migración",
     "Línea base, objetivo y Etapa 2 (ver docs/arquitectura/vision.md).",
     [["pl-base", "gp-estado", "pl-objetivo", "pl-etapa2"],
      ["wp-calidad", "wp-piloto", "ac-motor", "pr-ia"]]),
]

CAPA_DE_TIPO = {
    **dict.fromkeys(["Stakeholder", "Driver", "Assessment", "Goal", "Principle", "Requirement", "Constraint"], "Motivación"),
    **dict.fromkeys(["BusinessActor", "BusinessRole", "BusinessService", "BusinessProcess", "BusinessObject"], "Negocio"),
    **dict.fromkeys(["ApplicationComponent", "ApplicationInterface", "ApplicationService", "DataObject"], "Aplicación"),
    **dict.fromkeys(["Node", "SystemSoftware", "Device", "TechnologyService", "Artifact", "Path"], "Tecnología"),
    **dict.fromkeys(["Plateau", "Gap", "WorkPackage"], "Implementación y migración"),
}


def _esc(texto: str) -> str:
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def xml() -> str:
    tipos = {e[0]: e[1] for e in ELEMENTOS}
    s = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<!-- Generado por herramientas/archimate.py: no se edita a mano. -->',
         '<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"',
         '       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
         '       xsi:schemaLocation="http://www.opengroup.org/xsd/archimate/3.0/ '
         'http://www.opengroup.org/xsd/archimate/3.1/archimate3_Diagram.xsd"',
         '       identifier="id-till">',
         '  <name xml:lang="es">Till · red de cajeros de sucursal automatizados</name>',
         '  <documentation xml:lang="es">Implementación de referencia abierta (Etapa 1: todo lo físico y el banco son '
         'simulados). Modelo hecho con el código real del repositorio.</documentation>',
         '  <elements>']
    for ident, tipo, nombre, doc, codigo in ELEMENTOS:
        s.append(f'    <element identifier="{ident}" xsi:type="{tipo}">')
        s.append(f'      <name xml:lang="es">{_esc(nombre)}</name>')
        if doc:
            s.append(f'      <documentation xml:lang="es">{_esc(doc)}</documentation>')
        if codigo:
            s += ['      <properties>', '        <property propertyDefinitionRef="pd-codigo">',
                  f'          <value xml:lang="es">{_esc(codigo)}</value>', '        </property>', '      </properties>']
        s.append('    </element>')
    s += ['  </elements>', '  <relationships>']
    for ident, tipo, origen, destino, nombre in RELACIONES:
        extra = ' accessType="Write"' if tipo == "Access" and tipos[origen] in ("BusinessProcess", "ApplicationComponent") else ""
        if nombre:
            s += [f'    <relationship identifier="{ident}" source="{origen}" target="{destino}" xsi:type="{tipo}"{extra}>',
                  f'      <name xml:lang="es">{_esc(nombre)}</name>', '    </relationship>']
        else:
            s.append(f'    <relationship identifier="{ident}" source="{origen}" target="{destino}" xsi:type="{tipo}"{extra} />')
    s += ['  </relationships>',
          '  <propertyDefinitions>',
          '    <propertyDefinition identifier="pd-codigo" type="string">',
          '      <name xml:lang="es">código</name>',
          '    </propertyDefinition>',
          '  </propertyDefinitions>',
          '  <views>', '    <diagrams>']
    for vista, nombre, explicacion, filas in VISTAS:
        s += [f'      <view identifier="{vista}" xsi:type="Diagram">', f'        <name xml:lang="es">{_esc(nombre)}</name>',
              f'        <documentation xml:lang="es">{_esc(explicacion)}</documentation>']
        presentes = {}
        for fila, elementos in enumerate(filas):
            for col, elem in enumerate(elementos):
                nodo = f"{vista}-{elem}"
                presentes[elem] = nodo
                s.append(f'        <node identifier="{nodo}" elementRef="{elem}" xsi:type="Element" '
                         f'x="{20 + col * 260}" y="{20 + fila * 120}" w="220" h="70" />')
        for ident, _tipo, origen, destino, _nombre in RELACIONES:
            if origen in presentes and destino in presentes:
                s.append(f'        <connection identifier="{vista}-{ident}" relationshipRef="{ident}" xsi:type="Relationship" '
                         f'source="{presentes[origen]}" target="{presentes[destino]}" />')
        s.append('      </view>')
    s += ['    </diagrams>', '  </views>', '</model>']
    return "\n".join(s) + "\n"


FLECHA = {"Composition": "--*", "Aggregation": "--o", "Assignment": "-->", "Realization": "-.->", "Serving": "-->",
          "Access": "-.-", "Influence": "-.->", "Triggering": "==>", "Flow": "-.->", "Association": "---"}


def markdown() -> str:
    por_id = {e[0]: e for e in ELEMENTOS}
    s = ["# ArchiMate · vista legible",
         "",
         "> Generado por `herramientas/archimate.py` a partir del mismo modelo que "
         "[`till.archimate.xml`](till.archimate.xml) (formato de intercambio de The Open Group, ArchiMate 3.1). "
         "No se edita a mano: `python3 herramientas/archimate.py` regenera los dos archivos y "
         "`tests/test_archimate.py` falla si no coinciden.",
         "",
         f"El modelo tiene **{len(ELEMENTOS)} elementos**, **{len(RELACIONES)} relaciones** y **{len(VISTAS)} vistas**. "
         "Cada componente de aplicación y cada objeto de datos lleva la propiedad «código» con el archivo que lo "
         "implementa. Para abrirlo en una herramienta de modelado (p. ej. Archi): *File → Import → Open Exchange XML Model*.",
         "",
         "Notación en los diagramas: `--*` composición, `--o` agregación, `-->` asignación o servicio, `-.->` realización, "
         "influencia o flujo (el texto lo dice), `-.-` acceso, `==>` disparo, `---` asociación.",
         ""]
    for _vista, nombre, explicacion, filas in VISTAS:
        presentes = [e for fila in filas for e in fila]
        s += [f"## {nombre}", "", explicacion, "", "```mermaid", "flowchart TB"]
        for elem in presentes:
            _, tipo, etiqueta, _doc, _codigo = por_id[elem]
            s.append(f'    {elem.replace("-", "_")}["{etiqueta}<br/><i>{tipo}</i>"]')
        for _ident, tipo, origen, destino, rotulo in RELACIONES:
            if origen in presentes and destino in presentes:
                texto = f"{tipo.lower()}{' · ' + rotulo if rotulo else ''}"
                s.append(f'    {origen.replace("-", "_")} {FLECHA[tipo]}|"{texto}"| {destino.replace("-", "_")}')
        s += ["```", "", "| Elemento | Tipo | Código o fuente |", "|---|---|---|"]
        for elem in presentes:
            _, tipo, etiqueta, doc, codigo = por_id[elem]
            fuente = f"[`{codigo}`](../../{codigo})" if codigo else "—"
            s.append(f"| {etiqueta}{' — ' + doc if doc else ''} | {tipo} | {fuente} |")
        s.append("")
    return "\n".join(s)


def main() -> int:
    salidas = {XML: xml(), MD: markdown()}
    if "--revisar" in sys.argv[1:]:
        viejos = [p for p, texto in salidas.items() if not p.is_file() or p.read_text(encoding="utf-8") != texto]
        for p in viejos:
            print(f"{p.relative_to(RAIZ)}: DESACTUALIZADO (corre sin --revisar)")
        return 1 if viejos else 0
    for p, texto in salidas.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")
        print(f"{p.relative_to(RAIZ)}: escrito")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
