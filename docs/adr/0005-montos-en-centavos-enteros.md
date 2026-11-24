# ADR 0005 · Todo monto es un entero de centavos

- Estado: aceptada · Fecha: 2026-11-17 (commit `7b69349`, `till/dinero.py`)

## Contexto
El peso mexicano tiene dos decimales y el campo 4 de ISO 8583 viaja en unidades menores (centavos) con 12
dígitos. Los billetes se cuentan en piezas por denominación. Un error de redondeo en un cajero es un faltante
de efectivo.

## Decisión
Dentro de Till todo monto es un **entero de centavos** (`int`), del dispositivo al libro mayor; las
denominaciones van en pesos y una *mezcla* es denominación → piezas. El libro mayor rechaza cualquier monto
que no sea entero (flotante, `Decimal`, booleano). El texto para personas sale de `dinero.formatear`, que las
pruebas comparan contra `Decimal`.

## Alternativas descartadas
| Alternativa | Por qué no |
|---|---|
| **`float`** | Error binario de representación: 0.1 + 0.2 ≠ 0.3. Inaceptable para dinero. |
| **`Decimal` en todo el sistema** | Correcto, pero exige fijar contexto y redondeo en cada operación y convertir en cada frontera (ISO 8583, SQLite, JSON). Con una sola moneda de dos decimales y montos que nacen de contar billetes, el entero de centavos es exacto por construcción y más difícil de usar mal. `Decimal` queda como **oráculo** en las pruebas y en las cifras control del lote golden. |
| **Monto en pesos enteros** | El retiro sí es en pesos enteros, pero el banco, el saldo y la liquidación trabajan en centavos. |

## Consecuencias
- Sumas exactas en todas partes; el campo 4 es un formateo, no una conversión.
- Multimoneda (p. ej. divisas en un banco de comercio exterior) requeriría llevar la moneda y su exponente con
  cada monto (ISO 4217), no un tipo nuevo: queda fuera del alcance de la Etapa 1.
