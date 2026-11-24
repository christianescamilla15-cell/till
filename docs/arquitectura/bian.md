# Mapeo a BIAN

Correspondencia entre los componentes **reales** de Till y los dominios de servicio de **BIAN** (Banking
Industry Architecture Network). Los nombres y las descripciones se tomaron del paquete público de BIAN,
**release 14.0.0** (`bian-official/public`, carpeta `release14.0.0/semantic-apis`, un YAML por dominio de
servicio), consultado el 24-nov-2026. Cuando un nombre cambió entre versiones se dice.

Cómo leer la columna **Ajuste**:

- **Claro**: el componente hace lo que la descripción BIAN dice de ese dominio (a escala de un cajero).
- **Parcial**: cubre una parte; se dice cuál.
- **Sin equivalente**: no hay un dominio BIAN que le corresponda; se explica por qué. No se fuerza.

Till es la Etapa 1 de una red de cajeros con el banco **simulado**: los dominios del lado del banco
(autorización, cuenta, liquidación) se señalan para mostrar la frontera, no porque Till los implemente en
producción.

## Del lado del cajero y la sucursal

| Componente de Till | Código | Dominio BIAN (14.0) | Ajuste | Por qué |
|---|---|---|---|---|
| Máquina de estados del cajero | `till/core/maquina.py` | **ATM Network Operations** | Claro | BIAN: «Handling the automated operation of the ATM network and linked devices, including tracing physical cash and document movements». Es lo que hace la máquina de estados: opera los dispositivos y sigue cada billete (entregado, rechazo, retenido, escrow). |
| Dispositivos simulados (reciclador, lector, impresora, cámara) | `till/devices/` | **ATM Network Operations** | Parcial | Los «linked devices» del dominio. El modelo de comandos y eventos se inspira en XFS4IoT, que BIAN no define (es la capa de dispositivo, debajo de BIAN). |
| Comprobante | `till/core/maquina.py` (`_comprobante`) | **ATM Network Operations** | Parcial | El «document movement» del cajero. |
| Efectivo por casete y ubicación (cuentas `cajero:<t>:<ubicación>:<denominación>`) | `till/ledger/cuentas.py` | **Branch Currency Management** | Claro | BIAN: «Track cash inventory, project demand and ensure all cash is accounted for within the branch». El libro lleva cada denominación en cada ubicación del reciclador. |
| Estimación del día de reabasto (simulador de flota) | `till/fleet/simulador.py` | **Branch Currency Distribution** | Parcial | BIAN: «Schedule and coordinate the secure distribution of cash inventory across the branch and ATM networks». Till sólo **estima** el día de reabasto por cajero; no programa rutas de traslado de valores. |
| Métricas de flota (disponibilidad, fallas, efectivo) | `till/fleet/simulador.py`, `till/fleet/muestra.py` | — | Sin equivalente | Es una herramienta de simulación y planeación de ingeniería; BIAN describe dominios operativos, no simuladores. |
| Bitácora de transacciones e historial | `till/core/bitacora.py` | **Position Keeping** | Parcial | BIAN: «maintains a log of monetary or value transactions … Reconciled financial transactions are subsequently used for posting to the accounting systems». La bitácora es ese registro a escala del cajero (con escritura anticipada y estados); en un banco, Position Keeping vive del lado de la cuenta. |
| Libro mayor de partida doble | `till/ledger/libro.py` | **Financial Accounting** | Parcial | BIAN: «takes in financial facts and … creates accounting instructions that will update the general ledger and sub ledger accounts». Till lleva un **sub-libro por cajero** (efectivo, compensación con el banco, tránsito, aclaraciones); el libro general del banco queda fuera. |
| Cuadre a tres bandas | `till/recon/cuadre.py` | **Account Reconciliation** + **Branch Currency Management** | Parcial | «Account Reconciliation … handles account reconciliation tasks» (libro contra banco) y «ensure all cash is accounted for» (físico contra libro). Till une las dos en un solo reporte por cajero. |
| Efectivo retenido a nombre de la operación (aclaraciones) | `till/core/maquina.py` (`_registrar_retenido`), cuenta `aclaraciones:<t>` | **Customer Case Management** | Parcial | Till **registra** el efectivo retenido y lo deja en una cuenta puente; el caso con el cliente (seguimiento, resolución) no está implementado. |
| Traza ISO 8583 | `till/host/traza.py` | **Servicing Event History** | Parcial | BIAN: «captures, classifies and stores servicing activity and events to support root cause analysis». La traza guarda el diálogo con el banco (sin PAN ni campo 55) para diagnosticar; vive en memoria, no es un archivo histórico. |
| Tokenización del PAN y enmascarado | `till/seguridad.py` | **Issued Device Administration** | Parcial | BIAN: «administers the issuance of authentication tokens … such as cards». Till no emite tarjetas: sólo **usa** el token de una tarjeta ya emitida para no guardar el PAN. La tokenización como servicio técnico (bóveda/HSM) no es un dominio BIAN. |
| Criptograma EMV simulado (ARQC, ATC) | `till/seguridad.py`, `till/devices/tarjeta.py` | **Party Authentication** | Parcial | Autenticar la tarjeta (y en producción el PIN) es parte de «customer identify authentication … covering all channels and devices». En Till es un HMAC simulado, no EMV Book 2. |

## En el enlace y del lado del banco (simulado en Till)

| Componente de Till | Código | Dominio BIAN (14.0) | Ajuste | Por qué |
|---|---|---|---|---|
| Cliente ISO 8583 y enlace con el banco | `till/host/cliente.py`, `till/host/enlace.py`, `till/host/iso.py` | **Card Transaction Switch** | Parcial | BIAN: «orchestrates the switching and routing of Card Authorization and Financial transactions received through … the ATM Network from the Acquirer to the Issuer». Till es el **extremo del cajero** de ese diálogo (arma 0200, 0420/0421, 0800); el switch entre redes no existe aquí: el enlace va directo al banco simulado. |
| Reenvío de reversas (almacenar y reenviar) | `till/core/reversas.py` | **Card Transaction Switch** | Parcial | Las reversas son transacciones financieras de tarjeta que el adquirente debe entregar al emisor. El mecanismo de reintento con espera exponencial es del cajero. |
| Autorizador simulado: decisión 00/05/12/13/14/51/61 | `till/host/autorizador.py` | **Card Authorization** + **Current Account** | Parcial | BIAN 14.0 describe Card Authorization como «real time card authorization decisions for credit/charge cards»; Till autoriza una **tarjeta de débito** contra una cuenta de cheques, cuyo acceso por tarjeta de débito está en Current Account («payments and deposits … check and debit card access»). Es simulado: no es un emisor real. |
| Saldo, límite de retiro diario y día de negocio | `till/host/autorizador.py` | **Current Account** | Parcial | El saldo y el límite pertenecen a la cuenta. Simulado. |
| Pago de crédito en efectivo | `till/host/autorizador.py` (`pago_credito`) | **Loan** | Parcial | El abono reduce el adeudo de un crédito («completion of … ad-hoc product processing tasks»). Sin tasas, plazos ni calendario: sólo el adeudo. |
| Envío de remesa con clave de cobro | `till/host/autorizador.py` (`remesa`) | **Payment Order Initiation** | Parcial | BIAN 14.0: «captures the payer and payee details … and initiates the orchestration of the transaction». Till sólo genera la clave de cobro; no hay pago al beneficiario ni red de pagos. (En la release 13.0 existían **Payment Order**, **Payment Initiation** y **Payment Execution**; en la 14.0 ya no aparecen con esos nombres.) |
| Totales del banco para el cuadre | `till/host/autorizador.py` (`totales_terminal`) | **Card Financial Settlement** | Parcial | La liquidación entre adquirente y emisor. En Till el cuadre lee los totales del banco simulado; en producción llegarían por archivo de liquidación o mensajes de conciliación. |
| Límites y bloqueos de fraude (Etapa 2) | `docs/etapas.md` | **Fraud Evaluation** | Sin implementar | Sólo diseñado: «la IA propone, las reglas autorizan». No hay código de fraude en Till. |

## Lo que no es banca (no tiene dominio BIAN)

| Componente | Código | Por qué no hay equivalente |
|---|---|---|
| API HTTP de la demo y sus sesiones | `till/api/` | Es la fachada técnica para la demo web y las pruebas. Las sesiones aíslan la red simulada de cada visitante; no son sesiones de cliente bancario (BIAN **Session Dialogue** es otra cosa: el diálogo del cliente a través de canales). |
| Motor en el navegador (Pyodide, puente ASGI) | `web/src/motor/` | Infraestructura de ejecución de la demo. |
| Demo web | `web/` | Interfaz de demostración; en producción la pantalla del cajero sería parte de ATM Network Operations. |
| Armado de la red simulada | `till/red.py` | Composición de objetos para pruebas y demo. |

## Notas

- **ATM Network Management** no existe como dominio en la release 14.0 (sólo **ATM Network Operations**).
- **Fraud Detection** tampoco: en 14.0 los dominios de fraude son **Fraud Evaluation**, **Fraud Diagnosis**,
  **Fraud Model** y **Fraud Resolution**.
- Este mapeo es de componentes a dominios, no una certificación: los contratos BIAN (operaciones, «control
  records») no se implementan; el contrato de Till es su propio OpenAPI (`docs/api/openapi.json`).
