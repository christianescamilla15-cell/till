# ADR 0004 · Consistencia fuerte: bitácora y libro mayor en la misma base y la misma transacción

- Estado: aceptada · Fecha: 2026-11-17 (commits `15998b8` y `2e72902`)

## Contexto
El principio rector de Till es que **nunca hay cargo sin entrega ni entrega sin cargo**. Cuando el reciclador
confirma que el cliente tomó el efectivo, dos cosas tienen que quedar escritas juntas: el cambio de estado de
la operación (CONFIRMADA) y su asiento contable. Si el proceso muere entre una y otra, una consistencia
eventual dejaría una ventana donde el libro dice una cosa y la bitácora otra, justo en el momento que más
importa (un apagón a media entrega).

## Decisión
La bitácora de transacciones y el libro mayor viven en **el mismo archivo SQLite** en modo WAL con
`synchronous=FULL`, y cada cambio de estado con efecto contable se confirma con su asiento en **una sola
transacción** (`BaseDatos.transaccion()`, anidable). Además:
- **Escritura anticipada**: el STAN, la fecha y el RRN se guardan antes de mandar la 0200; la reversa se
  encola antes de intentarla, en el mismo paso atómico que el cambio a EN_REVERSA.
- **Sólo anexar**: triggers impiden UPDATE/DELETE en asientos, movimientos e historial.
- **Exactamente una vez**: llave de idempotencia por solicitud (con la huella de la solicitud), por asiento y
  por mensaje (el banco no aplica dos veces la misma 0200 ni la misma reversa).
- Al reiniciar, lo que no está en un estado final se retoma; lo que no se puede probar que se entregó, se
  revierte.

## Alternativas descartadas
| Alternativa | Por qué no |
|---|---|
| **Consistencia eventual** (bitácora local y libro en otro servicio, unidos por eventos o una saga) | Abre una ventana entre «se entregó el efectivo» y «se registró el asiento». Con apagones como falla normal del dominio, esa ventana se llena de aclaraciones. La compensación de una saga es justo lo que la reversa ISO 8583 ya hace con el banco, y no hace falta repetirla dentro del cajero. |
| **Dos bases (bitácora y libro) con *two-phase commit*** | Más piezas para el mismo resultado; SQLite en un archivo da atomicidad sin coordinador. |
| **Libro mutable (saldos que se actualizan)** | Pierde el rastro de auditoría; una corrección debe ser otro asiento. |

## Consecuencias
- El cuadre a tres bandas puede exigir exactitud: físico = libro = banco, y toda diferencia se explica por
  reversas en vuelo, efectivo en tránsito o aclaraciones.
- Escala por cajero (una base por cajero en producción, con réplica al centro de datos), no con una base
  central compartida: el diseño de flota lo asume.
- `synchronous=FULL` cuesta latencia de `fsync` (la mayor parte del tiempo de las pruebas); la muestra de flota
  usa `NORMAL` porque sólo simula.
