# Reglas de negocio → pruebas

Ésta es la compuerta real de calidad de Till (más que el % de cobertura, que el CI sólo reporta): cada regla del
cajero tiene al menos una prueba que falla si la regla se rompe. Las referencias son `archivo::prueba` desde la
raíz del repo. `tests/test_reglas_doc.py` corre en el CI y verifica que cada prueba citada exista, que no esté
marcada para saltarse y que ninguna regla se quede sin prueba.

Para agregar o cambiar una regla: primero la prueba (y, si toca un día completo, el oráculo de
`tests/golden/`), después el código y al final esta tabla. Los requisitos de `docs/arquitectura/requisitos.md`
apuntan a estas reglas.

Además de las pruebas de pytest, dos verificaciones corren fuera de Python (job `motor` y job `front` del CI):
`web/scripts/humo_motor.mjs` (la API real en Pyodide/WebAssembly bajo el puente del worker) y el E2E de
Playwright citado en A9.

## 1. Dinero y montos · `till/dinero.py`, `till/host/iso.py`

| # | Regla | Pruebas |
|---|---|---|
| M1 | Todo monto interno es un entero de centavos; el libro mayor rechaza flotantes, `Decimal` y booleanos | `tests/test_bordes.py::test_el_libro_mayor_solo_acepta_centavos_enteros`, `tests/test_bordes.py::test_el_libro_mayor_rechaza_decimal`, `tests/test_bordes.py::test_valor_de_una_mezcla_coincide_con_decimal` |
| M2 | El formato en pesos coincide con `Decimal` (signo, separador de miles, dos decimales) | `tests/test_dinero.py::test_formatear`, `tests/test_bordes.py::test_formatear_coincide_con_decimal` |
| M3 | La mezcla de billetes suma exacto, respeta existencias y el máximo de 40 billetes por entrega | `tests/test_dinero.py::test_mezcla_respeta_existencias_y_maximo`, `tests/test_dinero.py::test_mezcla_es_correcta_y_completa`, `tests/test_bordes.py::test_retiro_por_monto_y_denominaciones` |
| M4 | La mezcla prefiere billetes grandes (menos billetes) | `tests/test_dinero.py::test_mezcla_prefiere_billetes_grandes` |
| M5 | La búsqueda es completa: encuentra mezclas que el voraz no encuentra ($60 = 3 × $20) | `tests/test_dinero.py::test_mezcla_que_el_voraz_no_encuentra` |
| M6 | Restar mezclas nunca deja cantidades negativas; limpiar normaliza y quita ceros | `tests/test_dinero.py::test_restar_no_deja_mezclas_negativas_y_limpiar_quita_ceros` |
| M7 | Campo 4 de ISO 8583: 12 dígitos en centavos; fuera de rango es error | `tests/test_bordes.py::test_campo_4_en_sus_bordes`, `tests/test_bordes.py::test_campo_4_fuera_de_rango` |
| M8 | Campo 54 (saldo con signo) y campo 95 (montos de reemplazo) se leen igual que se escriben | `tests/test_bordes.py::test_campo_54_de_saldo_ida_y_vuelta_con_signo`, `tests/test_bordes.py::test_campo_95_de_reemplazo_ida_y_vuelta` |

## 2. Solicitud: se valida antes de tocar un dispositivo · `till/core/maquina.py`

| # | Regla | Pruebas |
|---|---|---|
| S1 | Sólo hay cuatro operaciones: retiro, depósito, pago de crédito y remesa | `tests/test_bordes.py::test_la_api_rechaza_entradas_fuera_de_rango_con_422_sin_operar` |
| S2 | El retiro es en pesos enteros y positivos | `tests/test_bordes.py::test_retiro_con_centavos_o_no_positivo_es_invalido` |
| S3 | Un ingreso trae billetes: denominación positiva → cantidad entera no negativa, al menos uno | `tests/test_bordes.py::test_ingreso_con_billetes_invalidos_se_rechaza_sin_empezar` |
| S4 | El total de un ingreso cabe en el campo 4; si no, 422 y el cajero sigue aceptando depósitos | `tests/test_bordes.py::test_el_total_maximo_del_campo_4_si_se_acepta`, `tests/test_bordes.py::test_deposito_que_no_cabe_en_el_campo_4_da_422_y_el_cajero_sigue_aceptando`, `tests/test_bordes.py::test_sin_validacion_el_deposito_gigante_dejaba_el_escrow_ocupado` |
| S5 | Pago de crédito y remesa llevan destino; el destino cabe en el campo 103 (≤ 28 ASCII imprimibles) | `tests/test_api_web.py::test_destino_fuera_del_campo_103_se_rechaza_antes_de_operar`, `tests/test_bordes.py::test_la_api_rechaza_entradas_fuera_de_rango_con_422_sin_operar` |
| S6 | La llave de idempotencia es obligatoria (encabezado `Idempotency-Key` o `llave_idem`) | `tests/test_api.py::test_retiro_idempotente_por_encabezado` |
| S7 | Sólo se aceptan PANs de prueba públicos que pasan Luhn | `tests/test_maquinas_de_estado.py::test_el_lector_solo_acepta_pans_de_prueba`, `tests/test_api.py::test_pan_no_permitido_y_cajero_inexistente` |

## 3. Exactamente una vez · bitácora, host y libro mayor

| # | Regla | Pruebas |
|---|---|---|
| I1 | La misma llave con la misma solicitud devuelve la operación original (`repetida`) sin segundo cargo | `tests/test_fallas.py::test_misma_llave_no_repite_la_operacion`, `tests/test_api.py::test_retiro_idempotente_por_encabezado`, `tests/test_bordes.py::test_ingreso_repetido_con_los_mismos_billetes_es_la_misma_operacion` |
| I2 | La misma llave con OTRA solicitud (monto, tipo, tarjeta o cajero) se rechaza (422) y no opera | `tests/test_bordes.py::test_llave_reutilizada_con_otra_solicitud_se_rechaza`, `tests/test_bordes.py::test_llave_reutilizada_por_api_da_422` |
| I3 | Solicitudes simultáneas con la misma llave producen una sola operación y una sola 0200 | `tests/test_bordes.py::test_solicitudes_simultaneas_con_la_misma_llave_cobran_una_vez` |
| I4 | El banco no aplica dos veces la misma 0200 (terminal, STAN, fecha de transmisión) | `tests/test_host.py::test_0200_repetida_no_carga_dos_veces` |
| I5 | El banco abona una sola vez aunque la reversa llegue repetida (0420/0421) | `tests/test_host.py::test_reversa_total_parcial_e_idempotente`, `tests/test_fallas.py::test_reversa_sin_acuse_se_reenvia_hasta_el_0430` |
| I6 | Cada asiento tiene llave: mismo contenido = mismo asiento; otro contenido = conflicto | `tests/test_libro_mayor.py::test_idempotencia_y_conflicto` |
| I7 | Recuperar dos veces no duplica asientos ni reversas | `tests/test_fallas.py::test_recuperar_dos_veces_no_duplica_nada`, `tests/test_maquinas_de_estado.py::test_la_recuperacion_cierra_cada_estado_pendiente` |

## 4. Máquina de estados de la operación · `till/core/bitacora.py`

| # | Regla | Pruebas |
|---|---|---|
| E1 | Sólo proceden las transiciones de `TRANSICIONES`; cualquier otra se rechaza sin tocar estado, campos ni historial (144 parejas) | `tests/test_maquinas_de_estado.py::test_cada_transicion_permitida_avanza_y_cada_invalida_se_rechaza_sin_cambios`, `tests/test_maquinas_de_estado.py::test_transicion_de_una_operacion_inexistente` |
| E2 | Los estados finales no tienen salida; todo estado es alcanzable y puede terminar | `tests/test_maquinas_de_estado.py::test_los_estados_finales_no_tienen_salida_y_todo_lo_demas_llega_a_uno` |
| E3 | El historial de transiciones es sólo-anexar | `tests/test_maquinas_de_estado.py::test_el_historial_de_transiciones_es_solo_anexar`, `tests/test_fallas.py::test_historial_de_transiciones` |
| E4 | Los diagramas de estados de la documentación dicen lo mismo que el código | `tests/test_maquinas_de_estado.py::test_el_diagrama_de_estados_documentado_es_el_del_codigo` |
| E5 | Un UPDATE de la bitácora sólo toca columnas de una lista fija (llave, huella, cajero e id no se reescriben) | `tests/test_maquinas_de_estado.py::test_la_bitacora_solo_actualiza_columnas_editables` |
| E6 | Escritura anticipada: STAN, fecha y RRN quedan en la bitácora antes de enviar la 0200; el STAN es consecutivo por cajero y da la vuelta en 999999 | `tests/test_maquinas_de_estado.py::test_el_stan_es_consecutivo_por_cajero_y_da_la_vuelta`, `tests/test_fallas.py::test_retiro_con_falla` |
| E7 | Una operación no cuenta hasta que el dispositivo confirma y el banco aprueba | `tests/test_fallas.py::test_retiro_con_falla`, `tests/test_fallas.py::test_ingreso_con_falla` |
| E8 | Al reiniciar, cada operación no final se cierra: sin 0200 se cancela; con 0200 y sin prueba física de entrega, se revierte | `tests/test_maquinas_de_estado.py::test_la_recuperacion_cierra_cada_estado_pendiente` |

## 5. Reversas: almacenar y reenviar · `till/core/reversas.py`

| # | Regla | Pruebas |
|---|---|---|
| R1 | Cualquier falla después de autorizar dispara una reversa total (0420) | `tests/test_fallas.py::test_retiro_con_falla` |
| R2 | Entrega parcial: reversa parcial con campo 95 y sólo se cobra lo entregado; con un solo billete es reversa total | `tests/test_bordes.py::test_entrega_parcial_cobra_solo_lo_entregado` |
| R3 | Sin 0200 no hay reversa; encolarla y pasar a EN_REVERSA es un solo paso atómico | `tests/test_maquinas_de_estado.py::test_no_hay_reversa_sin_0200`, `tests/test_maquinas_de_estado.py::test_encolar_reversa_desde_un_estado_que_no_la_admite_no_deja_nada` |
| R4 | Una reversa por operación: PENDIENTE → ACUSADA una vez; acusada no se reenvía | `tests/test_maquinas_de_estado.py::test_una_operacion_tiene_una_sola_reversa_y_se_acusa_una_vez`, `tests/test_maquinas_de_estado.py::test_una_reversa_acusada_no_se_vuelve_a_enviar` |
| R5 | Se reenvía (0421) hasta el 0430, aunque el proceso muera de por medio | `tests/test_fallas.py::test_reversa_sin_acuse_se_reenvia_hasta_el_0430`, `tests/test_traza.py::test_host_caido_anota_cada_reintento_hasta_el_0430` |
| R6 | Espera exponencial con tope entre reintentos; el ciclo de fondo respeta la espera | `tests/test_bordes.py::test_la_espera_entre_reintentos_es_exponencial_con_tope`, `tests/test_bordes.py::test_respetar_la_espera_no_reintenta_antes_de_tiempo`, `tests/test_api_web.py::test_reversa_con_enlace_caido_se_reintenta_respetando_la_espera` |
| R7 | El ciclo de reenvío nunca muere por un error puntual | `tests/test_bordes.py::test_el_ciclo_de_reenvio_sobrevive_a_un_error_puntual`, `tests/test_api.py::test_ciclo_de_fondo_reenvia_reversas_solo` |
| R8 | Una reversa sin original se acusa y bloquea la 0200 si llega tarde; si la 0210 se perdió (el banco sí aprobó), la reversa la sigue | `tests/test_host.py::test_reversa_sin_original_bloquea_la_0200_tardia`, `tests/test_traza.py::test_respuesta_perdida_queda_en_la_traza_y_la_reversa_la_sigue`, `tests/test_bordes.py::test_tiempo_agotado_real_con_el_banco` |
| R9 | Con el acuse de una reversa parcial, el efectivo en tránsito pasa a la compensación del banco | `tests/test_libro_mayor.py::test_operaciones_tipo_y_verificacion`, `tests/test_api.py::test_falla_parcial_por_api_y_detalle_de_operacion` |

## 6. Fallas, cortes y tiempo agotado · `till/devices/`, `till/host/enlace.py`

| # | Regla | Pruebas |
|---|---|---|
| F1 | Retiro: cada falla de la tabla «Qué pasa con cada falla» del README termina en su estado, con su cargo y con el efectivo correcto | `tests/test_fallas.py::test_retiro_con_falla` |
| F2 | Ingreso: si no se puede guardar el efectivo se revierte y se devuelve; si tampoco se puede devolver, queda en aclaración a nombre de la operación | `tests/test_fallas.py::test_ingreso_con_falla` |
| F3 | Tiempo agotado real con el banco: reversa; la 0200 vencida nunca se aplica y el efectivo de un depósito se devuelve | `tests/test_bordes.py::test_tiempo_agotado_real_con_el_banco`, `tests/test_bordes.py::test_deposito_con_tiempo_agotado_devuelve_el_efectivo` |
| F4 | Un corte de energía apaga todo el cajero; queda fuera de servicio hasta recuperar | `tests/test_maquinas_de_estado.py::test_fuera_de_servicio_rechaza_operar_hasta_recuperar`, `tests/test_api.py::test_corte_de_energia_y_reinicio` |
| F5 | Nada empieza en un cajero apagado, ni siquiera lo que esperaba turno cuando se fue la luz | `tests/test_bordes.py::test_operacion_que_espera_mientras_otra_apaga_el_cajero_no_empieza` |
| F6 | Una falla de impresora no revierte nada: sólo se anota | `tests/test_fallas.py::test_retiro_con_falla` |
| F7 | Si el lector o el chip fallan, la operación se cancela antes de autorizar | `tests/test_fallas.py::test_retiro_con_falla` |
| F8 | Sólo se programan fallas que aplican al comando | `tests/test_dispositivos.py::test_falla_no_soportada`, `tests/test_api.py::test_falla_invalida_da_422` |
| F9 | Cualquier secuencia de operaciones con fallas conserva las invariantes (libro, cuadre, saldo, efectivo, PAN) | `tests/test_propiedades.py::test_cualquier_secuencia_con_fallas_conserva_las_invariantes` |

## 7. Banco simulado · `till/host/autorizador.py`

| # | Regla | Pruebas |
|---|---|---|
| H1 | Fondos insuficientes: 51 y no se mueve efectivo | `tests/test_host.py::test_retiro_aprobado_y_fondos_insuficientes`, `tests/test_fallas.py::test_fondos_insuficientes_se_rechaza_sin_mover_efectivo` |
| H2 | Límite de retiro diario: hasta el límite exacto sí, un centavo más es 61; lo rechazado no cuenta | `tests/test_bordes.py::test_limite_diario_en_sus_bordes`, `tests/test_bordes.py::test_el_cajero_rechaza_con_61_sin_mover_efectivo` |
| H3 | El día de negocio es el de la Ciudad de México: el límite se reinicia a medianoche local (06:00 UTC todo el año) | `tests/test_bordes.py::test_el_limite_se_reinicia_a_medianoche_de_la_ciudad_de_mexico`, `tests/test_bordes.py::test_la_zona_local_de_iso_8583_es_la_de_la_ciudad_de_mexico` |
| H4 | La reversa del mismo día libera el límite; la de otro día devuelve el saldo pero no libera el límite de hoy | `tests/test_bordes.py::test_la_reversa_del_mismo_dia_libera_el_limite`, `tests/test_bordes.py::test_la_reversa_de_otro_dia_no_libera_el_limite_de_hoy` |
| H5 | El criptograma (ARQC) debe validar: alterado es 05; cada criptograma sube el contador ATC | `tests/test_host.py::test_criptograma_invalido_se_rechaza`, `tests/test_dispositivos.py::test_cada_criptograma_sube_el_contador_atc` |
| H6 | Pago de crédito: no más que el adeudo (13); remesa aprobada deja una clave de cobro | `tests/test_host.py::test_pago_de_credito_y_remesa` |
| H7 | Moneda distinta de MXN, proceso desconocido, monto cero o crédito inexistente: 12; tarjeta sin cuenta: 14; mensaje desconocido: 12 | `tests/test_host.py::test_el_host_rechaza_lo_que_no_procede_sin_mover_saldo`, `tests/test_host.py::test_tarjeta_sin_cuenta_en_el_banco_es_invalida`, `tests/test_host.py::test_mensaje_de_tipo_desconocido_se_contesta_como_invalido` |
| H8 | ISO 8583 se codifica y decodifica igual; por TCP con marco de 2 bytes; eco 0800/0810 | `tests/test_host.py::test_codificacion_ida_y_vuelta`, `tests/test_host.py::test_eco_y_transporte_tcp` |

## 8. Libro mayor · `till/ledger/`

| # | Regla | Pruebas |
|---|---|---|
| L1 | Partida doble: cada asiento suma cero y toca al menos dos cuentas | `tests/test_libro_mayor.py::test_asiento_debe_cuadrar` |
| L2 | Sólo anexar: ni UPDATE ni DELETE (una corrección es otro asiento) | `tests/test_libro_mayor.py::test_solo_anexar` |
| L3 | Asientos tipo de retiro, depósito, tránsito, reabasto y movimientos internos | `tests/test_libro_mayor.py::test_operaciones_tipo_y_verificacion` |
| L4 | Pase lo que pase, el libro queda balanceado | `tests/test_fallas.py::test_retiro_con_falla`, `tests/test_propiedades.py::test_cualquier_secuencia_con_fallas_conserva_las_invariantes` |

## 9. Efectivo y dispositivos · `till/devices/`

| # | Regla | Pruebas |
|---|---|---|
| D1 | El reciclador rechaza comandos fuera de su estado sin mover efectivo | `tests/test_maquinas_de_estado.py::test_reciclador_rechaza_comandos_fuera_de_su_estado_sin_mover_efectivo` |
| D2 | La memoria no volátil guarda la fase de cada operación; tras un corte, el transporte va a rechazo o a retenido | `tests/test_maquinas_de_estado.py::test_la_memoria_no_volatil_registra_la_fase_de_cada_entrega`, `tests/test_dispositivos.py::test_corte_de_energia_y_arranque` |
| D3 | Se aceptan las seis denominaciones MXN; lo demás se devuelve y no cuenta | `tests/test_bordes.py::test_cada_denominacion_mxn_se_acepta_en_deposito`, `tests/test_bordes.py::test_billetes_que_no_son_mxn_se_devuelven` |
| D4 | El depósito pasa por escrow; lo que no cabe en el casete va a excedente | `tests/test_dispositivos.py::test_deposito_con_escrow_y_excedente`, `tests/test_dispositivos.py::test_devolucion_de_escrow_trabada_queda_retenida` |
| D5 | Un atasco manda los billetes a rechazo: el efectivo cambia de lugar, no desaparece | `tests/test_dispositivos.py::test_atasco_manda_billetes_a_rechazo`, `tests/test_dispositivos.py::test_entrega_parcial` |
| D6 | El lector rechaza comandos fuera de su estado; la sesión de tarjeta vive de la lectura a la expulsión; al encender tras un corte captura la tarjeta | `tests/test_maquinas_de_estado.py::test_lector_rechaza_comandos_fuera_de_su_estado`, `tests/test_maquinas_de_estado.py::test_la_sesion_de_tarjeta_vive_de_la_lectura_a_la_expulsion`, `tests/test_maquinas_de_estado.py::test_apagado_el_lector_no_atiende_y_al_encender_captura_la_tarjeta` |
| D7 | Los dispositivos publican eventos de lo que pasa físicamente | `tests/test_dispositivos.py::test_entrega_normal_emite_eventos` |

## 10. Datos de tarjeta · `till/seguridad.py`, `till/host/traza.py`

| # | Regla | Pruebas |
|---|---|---|
| P1 | El PAN nunca se persiste (ni en la base ni en su WAL): sólo token con llave y enmascarado | `tests/test_fallas.py::test_retiro_con_falla`, `tests/test_dispositivos.py::test_lector_tokeniza_y_olvida_pan` |
| P2 | Lo que se muestra o se traza lleva el PAN enmascarado 6+4 y sin datos del chip (campo 55) | `tests/test_traza.py::test_retiro_deja_0200_y_0210_sin_pan_ni_chip`, `tests/test_host.py::test_codificacion_ida_y_vuelta` |
| P3 | El token es determinista para un PAN y depende de la llave | `tests/test_dispositivos.py::test_el_token_es_determinista_y_depende_de_la_llave` |
| P4 | La impresora se niega a imprimir algo que parezca un PAN completo | `tests/test_dispositivos.py::test_impresora_rechaza_pan_en_claro_y_camara_stub` |
| P5 | Ninguna respuesta de la API ni del motor del navegador trae el PAN en claro | `tests/test_api_web.py::test_tablero_y_detalle_con_mensajes_y_comprobante`, `tests/test_motor_puente.py::test_el_puente_arranca_la_app_y_atiende_un_retiro_completo` |

## 11. Cuadre de fin de día · `till/recon/cuadre.py`

| # | Regla | Pruebas |
|---|---|---|
| C1 | Sin diferencias, el cuadre a tres bandas CUADRA (físico = libro, libro = banco) | `tests/test_cuadre.py::test_cuadre_limpio_con_varias_operaciones` |
| C2 | Un faltante físico es DIFERENCIAS, con la cuenta y el monto | `tests/test_cuadre.py::test_cuadre_detecta_faltante_fisico` |
| C3 | Una diferencia con el banco que las reversas en vuelo no explican es DIFERENCIAS | `tests/test_cuadre.py::test_cuadre_detecta_diferencia_con_el_host` |
| C4 | Con reversas sin acuse el cuadre es PRELIMINAR | `tests/test_fallas.py::test_reversa_sin_acuse_se_reenvia_hasta_el_0430`, `tests/test_api.py::test_enlace_reversas_y_libro` |
| C5 | Billetes en escrow o transporte: DIFERENCIAS (se cuadra con el cajero en reposo) | `tests/test_cuadre.py::test_cuadre_con_billetes_en_escrow_no_cuadra` |
| C6 | No se cuadra un cajero apagado (503) | `tests/test_maquinas_de_estado.py::test_el_cuadre_de_un_cajero_apagado_da_503` |
| C7 | Un día completo coincide con el oráculo golden, por fila y en cifras control; una fila alterada se detecta | `tests/test_paridad_golden.py::test_paridad_del_dia_contra_el_lote_golden`, `tests/test_paridad_golden.py::test_cifras_control_del_dia`, `tests/test_paridad_golden.py::test_control_negativo_una_fila_alterada_se_detecta`, `tests/test_paridad_golden.py::test_control_negativo_filas_que_faltan_o_sobran` |

## 12. Comprobante · `till/core/maquina.py`

| # | Regla | Pruebas |
|---|---|---|
| K1 | El comprobante queda ligado a su operación y sin PAN completo | `tests/test_traza.py::test_comprobante_ligado_a_su_operacion` |
| K2 | La remesa imprime su clave de cobro; el efectivo retenido imprime su folio de aclaración | `tests/test_traza.py::test_comprobante_de_remesa_con_clave_y_de_retenido_con_aclaracion` |

## 13. Flota · `till/fleet/`

| # | Regla | Pruebas |
|---|---|---|
| FL1 | El modelo agregado conserva el efectivo y sus métricas son coherentes (también por la API, por sesión) | `tests/test_flota.py::test_flota_200_cajeros_metricas_coherentes`, `tests/test_api.py::test_deposito_y_flota` |
| FL2 | Para una semilla, el resultado es determinista | `tests/test_flota.py::test_flota_es_determinista_por_semilla` |
| FL3 | De 1 a 15 000 cajeros | `tests/test_flota.py::test_flota_valida_parametros`, `tests/test_bordes.py::test_la_api_rechaza_entradas_fuera_de_rango_con_422_sin_operar` |
| FL4 | La muestra de alta fidelidad (máquina de estados real con fallas al azar) cuadra en todos los cajeros | `tests/test_flota.py::test_muestra_de_alta_fidelidad_cuadra` |

## 14. API, demo y motor · `till/api/`, `api/index.py`, `web/src/motor/`

| # | Regla | Pruebas |
|---|---|---|
| A1 | Una red por sesión, aislada; identificador válido; tope con desalojo de la menos reciente; purga por inactividad | `tests/test_api_web.py::test_sesiones_aisladas`, `tests/test_api_web.py::test_sesion_invalida_y_tope_de_sesiones`, `tests/test_maquinas_de_estado.py::test_sesion_nueva_activa_y_purgada_por_inactividad`, `tests/test_maquinas_de_estado.py::test_la_sesion_usada_hace_poco_no_se_desaloja`, `tests/test_maquinas_de_estado.py::test_identificador_de_sesion_invalido` |
| A2 | El contrato OpenAPI versionado está al día, cada operación documentada, el mismo bajo `/api`; el front sólo llama rutas del contrato | `tests/test_contrato_openapi.py::test_el_contrato_versionado_esta_al_dia`, `tests/test_contrato_openapi.py::test_cada_operacion_esta_documentada`, `tests/test_contrato_openapi.py::test_la_demo_monta_el_mismo_contrato_bajo_api`, `tests/test_contrato_openapi.py::test_el_front_solo_llama_rutas_del_contrato` |
| A3 | Entradas fuera de rango: 422 sin empezar nada | `tests/test_bordes.py::test_la_api_rechaza_entradas_fuera_de_rango_con_422_sin_operar` |
| A4 | La entrada local sirve con la misma CSP y encabezados que `vercel.json`; `.vercelignore` va anclado (no se lleva `till/api/`) | `tests/test_vercel.py::test_api_bajo_prefijo_con_encabezados`, `tests/test_vercel.py::test_vercel_json_coincide_con_la_entrada`, `tests/test_vercel.py::test_sirve_el_front_construido_en_local`, `tests/test_vercel.py::test_bases_en_el_directorio_de_datos` |
| A5 | El puente ASGI del worker corre la misma app y entrega respuestas y errores tal cual | `tests/test_motor_puente.py::test_el_puente_arranca_la_app_y_atiende_un_retiro_completo`, `tests/test_motor_puente.py::test_el_puente_entrega_los_errores_de_la_api_tal_cual`, `tests/test_motor_puente.py::test_el_puente_es_el_que_carga_el_worker` |
| A6 | El motor del navegador trae todo lo que importa `till/` (incluida la base de zonas horarias) y sus módulos esenciales | `tests/test_arquitectura.py::test_todo_lo_que_importa_till_existe_en_el_motor_de_la_demo`, `tests/test_arquitectura.py::test_los_modulos_esenciales_del_motor_existen` |
| A7 | Capas: nadie importa una capa superior; el dominio no conoce la API ni el transporte web | `tests/test_arquitectura.py::test_ninguna_capa_importa_a_una_superior`, `tests/test_arquitectura.py::test_el_dominio_no_conoce_la_api_ni_el_transporte_web`, `tests/test_arquitectura.py::test_el_nucleo_transaccional_no_depende_de_la_flota_ni_del_armado`, `tests/test_arquitectura.py::test_el_puente_del_navegador_y_la_entrada_local_solo_entran_por_la_app`, `tests/test_arquitectura.py::test_se_revisaron_todos_los_modulos` |
| A8 | Una base de datos por red en el directorio de datos; en Vercel, el temporal | `tests/test_api_web.py::test_directorio_de_datos_en_vercel`, `tests/test_api_web.py::test_rutas_bajo_api` |
| A9 | Un retiro completo en un navegador real sobre el build: sin errores de consola, sin violaciones de CSP, sin PAN en pantalla y sin llamadas a un servidor de API | `e2e/retiro.e2e.js::retiro completo de $2,500: tarjeta, monto, efectivo, comprobante y cuadre` |
| A10 | La salud reporta cajeros fuera de servicio, libro balanceado y reversas pendientes; el catálogo da tarjetas de prueba, denominaciones y fallas aplicables | `tests/test_api.py::test_salud_y_catalogo` |
