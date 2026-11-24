# Lote golden de un día de operación

`lote_dia.csv` es el **oráculo independiente** de `tests/test_paridad_golden.py`: cada fila se escribió a mano
desde la tabla «Qué pasa con cada falla» del README y las reglas de `docs/reglas.md`, **no** copiando lo que
produce el código. Si el código y el oráculo difieren, la prueba dice en qué llave y en qué columna.

- `cargo`: lo que el banco le cobra a la tarjeta por esa operación (pesos, dos decimales).
- `abono`: lo que el banco le abona a la cuenta de la tarjeta (sólo depósitos confirmados).
- `aclaracion`: efectivo del cliente que el cajero retuvo y queda a nombre de la operación.

`lote_dia.control.json` guarda las **cifras control** del lote (conteo por estado y sumas en `Decimal`), calculadas
a mano a partir de las filas; la prueba las recalcula de lo que pasó y también deben coincidir. El listón lo pone
un **control negativo**: la misma comparación, con una fila alterada, debe fallar y señalar esa fila.

Para cambiar una regla: primero se corrige el oráculo (y sus cifras control), después el código.
