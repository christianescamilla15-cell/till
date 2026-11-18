"""Libro mayor de partida doble, sólo-anexar, con llaves de idempotencia.

* Cada asiento tiene dos o más movimientos que suman exactamente cero
  (débito positivo, crédito negativo, en centavos).
* Nada se actualiza ni se borra: triggers de SQLite lo impiden. Una corrección
  es otro asiento.
* Cada asiento lleva una llave de idempotencia única. Registrar dos veces la
  misma llave con el mismo contenido regresa el asiento original; con
  contenido distinto es un error (alguien reutilizó la llave).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from till.db import BaseDatos

ESQUEMA = """
CREATE TABLE IF NOT EXISTS asientos (
    id          INTEGER PRIMARY KEY,
    llave_idem  TEXT NOT NULL UNIQUE,
    tipo        TEXT NOT NULL,
    referencia  TEXT,
    huella      TEXT NOT NULL,
    creado      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS movimientos (
    id         INTEGER PRIMARY KEY,
    asiento_id INTEGER NOT NULL REFERENCES asientos(id),
    cuenta     TEXT NOT NULL,
    monto      INTEGER NOT NULL CHECK (monto <> 0)
);
CREATE INDEX IF NOT EXISTS ix_movimientos_cuenta ON movimientos(cuenta);
CREATE INDEX IF NOT EXISTS ix_movimientos_asiento ON movimientos(asiento_id);
CREATE INDEX IF NOT EXISTS ix_asientos_referencia ON asientos(referencia);
CREATE TRIGGER IF NOT EXISTS asientos_solo_anexar_u BEFORE UPDATE ON asientos
    BEGIN SELECT RAISE(ABORT, 'libro mayor: solo anexar'); END;
CREATE TRIGGER IF NOT EXISTS asientos_solo_anexar_d BEFORE DELETE ON asientos
    BEGIN SELECT RAISE(ABORT, 'libro mayor: solo anexar'); END;
CREATE TRIGGER IF NOT EXISTS movimientos_solo_anexar_u BEFORE UPDATE ON movimientos
    BEGIN SELECT RAISE(ABORT, 'libro mayor: solo anexar'); END;
CREATE TRIGGER IF NOT EXISTS movimientos_solo_anexar_d BEFORE DELETE ON movimientos
    BEGIN SELECT RAISE(ABORT, 'libro mayor: solo anexar'); END;
"""

Movimiento = tuple[str, int]


class AsientoDescuadrado(ValueError):
    pass


class ConflictoIdempotencia(Exception):
    pass


def _huella(tipo: str, movimientos: list[Movimiento]) -> str:
    cuerpo = json.dumps([tipo, sorted(movimientos)], separators=(",", ":"))
    return hashlib.sha256(cuerpo.encode()).hexdigest()


def _consolidar(movimientos: Iterable[Movimiento]) -> list[Movimiento]:
    acumulado: dict[str, int] = {}
    for cuenta, monto in movimientos:
        if not isinstance(monto, int) or isinstance(monto, bool):
            raise AsientoDescuadrado(f"monto no entero en {cuenta}: {monto!r}")
        acumulado[cuenta] = acumulado.get(cuenta, 0) + monto
    return sorted((c, m) for c, m in acumulado.items() if m != 0)


class LibroMayor:
    def __init__(self, db: BaseDatos):
        self.db = db
        db.crear_esquema(ESQUEMA)

    def registrar(
        self,
        llave_idem: str,
        tipo: str,
        movimientos: Iterable[Movimiento],
        referencia: str | None = None,
    ) -> int:
        lineas = _consolidar(movimientos)
        if len(lineas) < 2:
            raise AsientoDescuadrado("un asiento necesita al menos dos cuentas")
        suma = sum(m for _, m in lineas)
        if suma != 0:
            raise AsientoDescuadrado(f"el asiento {llave_idem} no cuadra: suma {suma}")
        huella = _huella(tipo, lineas)
        with self.db.transaccion() as conn:
            previo = conn.execute("SELECT id, huella FROM asientos WHERE llave_idem = ?", (llave_idem,)).fetchone()
            if previo is not None:
                if previo["huella"] != huella:
                    raise ConflictoIdempotencia(f"la llave {llave_idem} ya se usó con otro contenido")
                return int(previo["id"])
            cur = conn.execute(
                "INSERT INTO asientos (llave_idem, tipo, referencia, huella, creado) VALUES (?, ?, ?, ?, ?)",
                (llave_idem, tipo, referencia, huella, datetime.now(timezone.utc).isoformat()),
            )
            asiento_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO movimientos (asiento_id, cuenta, monto) VALUES (?, ?, ?)",
                [(asiento_id, c, m) for c, m in lineas],
            )
            return asiento_id

    def existe(self, llave_idem: str) -> bool:
        return self.db.uno("SELECT 1 FROM asientos WHERE llave_idem = ?", (llave_idem,)) is not None

    # -- consultas ---------------------------------------------------------------
    def saldo(self, cuenta: str) -> int:
        fila = self.db.uno("SELECT COALESCE(SUM(monto), 0) AS s FROM movimientos WHERE cuenta = ?", (cuenta,))
        return int(fila["s"]) if fila else 0

    def saldos(self, prefijo: str = "") -> dict[str, int]:
        filas = self.db.consultar(
            "SELECT cuenta, SUM(monto) AS s FROM movimientos WHERE substr(cuenta, 1, ?) = ? "
            "GROUP BY cuenta ORDER BY cuenta",
            (len(prefijo), prefijo),
        )
        return {f["cuenta"]: int(f["s"]) for f in filas}

    def asientos(self, referencia: str | None = None, limite: int = 100) -> list[dict[str, Any]]:
        if referencia is None:
            cab = self.db.consultar("SELECT * FROM asientos ORDER BY id DESC LIMIT ?", (limite,))
        else:
            cab = self.db.consultar(
                "SELECT * FROM asientos WHERE referencia = ? ORDER BY id DESC LIMIT ?", (referencia, limite)
            )
        r = []
        for a in cab:
            movs = self.db.consultar("SELECT cuenta, monto FROM movimientos WHERE asiento_id = ? ORDER BY id", (a["id"],))
            r.append(
                {
                    "id": a["id"],
                    "llave_idem": a["llave_idem"],
                    "tipo": a["tipo"],
                    "referencia": a["referencia"],
                    "creado": a["creado"],
                    "movimientos": [{"cuenta": m["cuenta"], "monto": m["monto"]} for m in movs],
                }
            )
        return r

    def verificar(self) -> dict[str, Any]:
        """Comprueba la partida doble sobre todo el libro."""
        total = self.db.uno("SELECT COALESCE(SUM(monto), 0) AS s, COUNT(*) AS n FROM movimientos")
        descuadrados = self.db.consultar(
            "SELECT asiento_id, SUM(monto) AS s FROM movimientos GROUP BY asiento_id HAVING SUM(monto) <> 0"
        )
        n_asientos = self.db.uno("SELECT COUNT(*) AS n FROM asientos")
        suma = int(total["s"]) if total else 0
        return {
            "balanceado": suma == 0 and not descuadrados,
            "suma_total": suma,
            "asientos": int(n_asientos["n"]) if n_asientos else 0,
            "movimientos": int(total["n"]) if total else 0,
            "asientos_descuadrados": [int(f["asiento_id"]) for f in descuadrados],
        }

