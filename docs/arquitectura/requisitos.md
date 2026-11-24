# Requisitos y trazabilidad

Cada requisito apunta a los **componentes** que lo implementan (archivos reales), a las **reglas** de
[`../reglas.md`](../reglas.md) que lo concretan y, a través de ellas, a sus **pruebas**. La cadena completa es
requisito → componente → regla → prueba; `tests/test_reglas_doc.py` verifica que cada regla citada aquí exista
en la tabla (y la tabla, que cada prueba exista y no se salte) y que cada componente citado exista.

Estado: **cumple** (probado en el CI), **parcial** (probado en lo que Till simula; lo demás está fuera de la
Etapa 1 y se dice) o **no medido** (sólo hay una meta propuesta).

## Funcionales

| # | Requisito | Componentes | Reglas | Estado |
|---|---|---|---|---|
| RF-01 | Retiro de efectivo con tarjeta: denominar, autorizar, entregar y confirmar sólo cuando el cliente toma el efectivo | `till/core/maquina.py`, `till/devices/efectivo.py`, `till/dinero.py` | S1, S2, S6, M3, M4, M5, M6, E7, F1, F7, D1, D5, D6 | cumple |
| RF-02 | Depósito, pago de crédito y remesa en efectivo con escrow: contar, autorizar y guardar | `till/core/maquina.py`, `till/devices/efectivo.py`, `till/host/autorizador.py` | S3, S4, S5, D3, D4, F2, H6 | cumple |
| RF-03 | Toda falla después de autorizar se revierte por lo que no se entregó (total o parcial) | `till/core/reversas.py`, `till/core/bitacora.py`, `till/host/cliente.py` | R1, R2, R3, R4, R5, R8, R9 | cumple |
| RF-04 | Recuperación tras un corte: cerrar cada operación pendiente sin perder ni duplicar | `till/core/maquina.py`, `till/devices/efectivo.py` | E8, F4, F5, I7, D2 | cumple |
| RF-05 | Idempotencia: repetir la solicitud no repite la operación; reutilizar la llave con otra solicitud es un error | `till/core/maquina.py`, `till/core/bitacora.py`, `till/ledger/libro.py`, `till/host/autorizador.py` | I1, I2, I3, I4, I5, I6 | cumple |
| RF-06 | Cuadre de fin de día a tres bandas con explicación de diferencias | `till/recon/cuadre.py`, `till/ledger/cuentas.py` | C1, C2, C3, C4, C5, C6, C7, L3 | cumple |
| RF-07 | Comprobante ligado a su operación, con clave de cobro o folio de aclaración cuando aplica | `till/core/maquina.py`, `till/devices/impresora.py` | K1, K2, F6 | cumple |
| RF-08 | Reglas del banco: fondos, límite diario por día de negocio, criptograma, crédito, moneda | `till/host/autorizador.py`, `till/seguridad.py` | H1, H2, H3, H4, H5, H7, H8 | parcial (banco simulado) |
| RF-09 | Operación de la red: inyectar fallas, reiniciar, eco, salud, traza | `till/api/app.py`, `till/devices/base.py`, `till/host/traza.py` | F8, A10, R8, D7 | cumple |
| RF-10 | Simulación de flota hasta 15 000 cajeros y muestra con la máquina real | `till/fleet/simulador.py`, `till/fleet/muestra.py` | FL1, FL2, FL3, FL4 | cumple |
| RF-11 | Demo pública: una red aislada por visitante, la misma API en el navegador | `till/api/sesiones.py`, `web/src/motor/puente.py`, `web/src/motor/motor.worker.js` | A1, A5, A6, A8, A9 | cumple |

## No funcionales

| # | Requisito | Componentes | Reglas | Estado |
|---|---|---|---|---|
| RNF-01 | **Disponibilidad**: un corte deja fuera de servicio sólo a ese cajero y se recupera solo al reiniciar; meta propuesta de ≥ 98 % por cajero en el piloto | `till/core/maquina.py`, `till/fleet/simulador.py` | F4, E8, FL1 | parcial: la recuperación se prueba; la disponibilidad real no está medida (sólo simulada) |
| RNF-02 | **Latencia y tiempo agotado**: espera configurable de la 0210 (2 s por omisión); sin respuesta a tiempo, reversa; reintentos con espera exponencial y tope; meta propuesta p99 falla → 0430 < 5 min | `till/host/enlace.py`, `till/core/reversas.py` | F3, R6, R7 | parcial: el mecanismo se prueba; el p99 real no está medido |
| RNF-03 | **Consistencia**: estado y asiento en una sola transacción; el libro siempre balanceado; exactamente una vez | `till/db.py`, `till/core/bitacora.py`, `till/ledger/libro.py` | E1, E2, E5, E6, L1, L4, F9, I3 | cumple |
| RNF-04 | **Durabilidad**: lo confirmado sobrevive a un corte (WAL, `synchronous=FULL`, escritura anticipada) | `till/db.py`, `till/core/bitacora.py` | E6, E8, R5 | cumple (simulado: el proceso muere, el disco no) |
| RNF-05 | **Seguridad de datos de tarjeta (PCI)**: PAN tokenizado en el borde, enmascarado 6+4, nunca persistido; CSP estricta; escaneos en cada cambio | `till/seguridad.py`, `till/devices/tarjeta.py`, `till/host/traza.py`, `vercel.json`, `api/index.py` | P1, P2, P3, P4, P5, A4, S7 | parcial: sin PIN, EPP, DUKPT, HSM ni MAC (Etapa 1) |
| RNF-06 | **Auditoría**: historial de transiciones y libro sólo-anexar; traza de mensajes sin datos sensibles; comprobante | `till/core/bitacora.py`, `till/ledger/libro.py`, `till/host/traza.py` | E3, L2, P2, K1 | cumple |
| RNF-07 | **Exactitud monetaria**: centavos enteros de punta a punta, comparados contra `Decimal`; cifras control por lote | `till/dinero.py`, `till/host/iso.py`, `till/ledger/libro.py` | M1, M2, M7, M8, C7 | cumple |
| RNF-08 | **Mismo comportamiento en los dos runtimes**: CPython 3.12 (local, pruebas) y Pyodide con Python 3.14 (demo publicada) | `web/scripts/preparar_motor.mjs`, `herramientas/requisitos_motor.py` | A5, A6, H3 | cumple (job `motor` del CI) |
| RNF-09 | **Mantenibilidad**: capas sin dependencias hacia arriba, contrato OpenAPI versionado, una prueba por regla | `till/`, `docs/api/openapi.json` | A2, A3, A7, E4 | cumple |
| RNF-10 | **Accesibilidad de la demo**: contraste AA, foco visible, operable con teclado, celular sin desborde | `web/src/`, `web/scripts/recorrido.mjs` | A9 | parcial: el recorrido completo (teclado, celular) es manual con `recorrido.mjs`; el CI corre el E2E del retiro |

## Matriz requisito → prueba (resumen)

Las pruebas exactas de cada regla están en [`../reglas.md`](../reglas.md). Por requisito, las más representativas:

| Requisito | Prueba representativa |
|---|---|
| RF-01 | `tests/test_fallas.py::test_retiro_con_falla`, `e2e/retiro.e2e.js` |
| RF-03 | `tests/test_bordes.py::test_entrega_parcial_cobra_solo_lo_entregado` |
| RF-04 | `tests/test_maquinas_de_estado.py::test_la_recuperacion_cierra_cada_estado_pendiente` |
| RF-05 | `tests/test_bordes.py::test_llave_reutilizada_con_otra_solicitud_se_rechaza` |
| RF-06 | `tests/test_paridad_golden.py::test_paridad_del_dia_contra_el_lote_golden` |
| RNF-03 | `tests/test_propiedades.py::test_cualquier_secuencia_con_fallas_conserva_las_invariantes` |
| RNF-05 | `tests/test_traza.py::test_retiro_deja_0200_y_0210_sin_pan_ni_chip` |
| RNF-08 | `tests/test_motor_puente.py::test_el_puente_arranca_la_app_y_atiende_un_retiro_completo` y `web/scripts/humo_motor.mjs` |
