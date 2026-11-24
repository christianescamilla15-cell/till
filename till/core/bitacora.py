"""Bitácora de transacciones en SQLite (WAL).

Es la memoria que sobrevive a un corte. Antes de cada paso irreversible se
escribe la intención (write-ahead): el STAN y la fecha de la 0200 se guardan
antes de enviarla, y la reversa se encola antes de intentar enviarla. Al
reiniciar, todo lo que no esté en un estado final se retoma.

El historial de transiciones es sólo-anexar (rastro de auditoría).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from till.db import BaseDatos


class Estado(StrEnum):
    INICIADA = "INICIADA"
    CONTANDO = "CONTANDO"  # ingresos: billetes en escrow, antes de autorizar
    AUTORIZANDO = "AUTORIZANDO"  # 0200 enviada (o por enviarse)
    AUTORIZADA = "AUTORIZADA"  # 0210 aprobada
    ENTREGANDO = "ENTREGANDO"  # retiro: el dispositivo entrega
    RECIBIENDO = "RECIBIENDO"  # ingreso: el dispositivo guarda el escrow
    CONFIRMADA = "CONFIRMADA"
    RECHAZADA = "RECHAZADA"
    CANCELADA = "CANCELADA"
    EN_REVERSA = "EN_REVERSA"
    REVERTIDA = "REVERTIDA"
    PARCIAL = "PARCIAL"


FINALES = frozenset({Estado.CONFIRMADA, Estado.RECHAZADA, Estado.CANCELADA, Estado.REVERTIDA, Estado.PARCIAL})

TRANSICIONES: dict[Estado, frozenset[Estado]] = {
    Estado.INICIADA: frozenset({Estado.CONTANDO, Estado.AUTORIZANDO, Estado.CANCELADA}),
    Estado.CONTANDO: frozenset({Estado.AUTORIZANDO, Estado.CANCELADA}),
    Estado.AUTORIZANDO: frozenset({Estado.AUTORIZADA, Estado.RECHAZADA, Estado.EN_REVERSA}),
    Estado.AUTORIZADA: frozenset({Estado.ENTREGANDO, Estado.RECIBIENDO, Estado.EN_REVERSA}),
    Estado.ENTREGANDO: frozenset({Estado.CONFIRMADA, Estado.EN_REVERSA}),
    Estado.RECIBIENDO: frozenset({Estado.CONFIRMADA, Estado.EN_REVERSA}),
    Estado.EN_REVERSA: frozenset({Estado.REVERTIDA, Estado.PARCIAL}),
}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS transacciones (
    id            TEXT PRIMARY KEY,
    llave_idem    TEXT NOT NULL UNIQUE,
    huella        TEXT,  -- sha256 de la solicitud: la misma llave con otra solicitud es un error
    terminal      TEXT NOT NULL,
    tipo          TEXT NOT NULL,
    estado        TEXT NOT NULL,
    monto         INTEGER NOT NULL DEFAULT 0,
    mezcla        TEXT NOT NULL DEFAULT '{}',
    destino       TEXT,
    token         TEXT,
    enmascarado   TEXT,
    stan          TEXT,
    f7            TEXT,
    rrn           TEXT,
    respuesta     TEXT,
    autorizacion  TEXT,
    saldo         INTEGER,
    entregado     INTEGER NOT NULL DEFAULT 0,
    aclaracion    INTEGER NOT NULL DEFAULT 0,
    motivo        TEXT,
    evidencia     TEXT,
    comprobante   TEXT,
    creado        TEXT NOT NULL,
    actualizado   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tx_terminal_estado ON transacciones(terminal, estado);

CREATE TABLE IF NOT EXISTS historial (
    id       INTEGER PRIMARY KEY,
    tx_id    TEXT NOT NULL REFERENCES transacciones(id),
    de       TEXT,
    a        TEXT NOT NULL,
    detalle  TEXT,
    momento  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_historial_tx ON historial(tx_id);
CREATE TRIGGER IF NOT EXISTS historial_solo_anexar_u BEFORE UPDATE ON historial
    BEGIN SELECT RAISE(ABORT, 'historial: solo anexar'); END;
CREATE TRIGGER IF NOT EXISTS historial_solo_anexar_d BEFORE DELETE ON historial
    BEGIN SELECT RAISE(ABORT, 'historial: solo anexar'); END;

CREATE TABLE IF NOT EXISTS reversas (
    id              INTEGER PRIMARY KEY,
    tx_id           TEXT NOT NULL UNIQUE REFERENCES transacciones(id),
    terminal        TEXT NOT NULL,
    tipo            TEXT NOT NULL,
    monto           INTEGER NOT NULL,
    reemplazo       INTEGER NOT NULL DEFAULT 0,
    stan            TEXT NOT NULL,
    f7              TEXT NOT NULL,
    rrn             TEXT NOT NULL,
    motivo          TEXT NOT NULL,
    estado          TEXT NOT NULL DEFAULT 'PENDIENTE',
    intentos        INTEGER NOT NULL DEFAULT 0,
    ultimo_error    TEXT,
    proximo_intento REAL NOT NULL DEFAULT 0,
    creada          TEXT NOT NULL,
    acusada         TEXT
);
CREATE INDEX IF NOT EXISTS ix_reversas_estado ON reversas(estado, proximo_intento);

CREATE TABLE IF NOT EXISTS secuencias (
    terminal TEXT PRIMARY KEY,
    stan     INTEGER NOT NULL
);
"""


class TransicionInvalida(Exception):
    pass


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fila(r: Any) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    if "mezcla" in d:
        d["mezcla"] = {int(k): v for k, v in json.loads(d["mezcla"] or "{}").items()}
    return d


class Bitacora:
    def __init__(self, db: BaseDatos):
        self.db = db
        db.crear_esquema(ESQUEMA)
        columnas = {f["name"] for f in db.consultar("PRAGMA table_info(transacciones)")}
        if "huella" not in columnas:  # base creada antes de la huella de la solicitud
            db.ejecutar("ALTER TABLE transacciones ADD COLUMN huella TEXT")

    # -- transacciones ---------------------------------------------------------
    def crear(
        self, terminal: str, tipo: str, llave_idem: str, destino: str | None = None, monto: int = 0,
        huella: str | None = None,
    ) -> dict[str, Any]:
        tx_id = uuid.uuid4().hex
        ahora = _ahora()
        with self.db.transaccion() as c:
            c.execute(
                "INSERT INTO transacciones (id, llave_idem, huella, terminal, tipo, estado, monto, destino, creado, actualizado) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tx_id, llave_idem, huella, terminal, tipo, Estado.INICIADA.value, monto, destino, ahora, ahora),
            )
            c.execute(
                "INSERT INTO historial (tx_id, de, a, detalle, momento) VALUES (?, NULL, ?, ?, ?)",
                (tx_id, Estado.INICIADA.value, tipo, ahora),
            )
        return self.obtener(tx_id)  # type: ignore[return-value]

    def obtener(self, tx_id: str) -> dict[str, Any] | None:
        return _fila(self.db.uno("SELECT * FROM transacciones WHERE id = ?", (tx_id,)))

    def por_llave(self, llave_idem: str) -> dict[str, Any] | None:
        return _fila(self.db.uno("SELECT * FROM transacciones WHERE llave_idem = ?", (llave_idem,)))

    def _set(self, campos: dict[str, Any]) -> tuple[str, list[Any]]:
        cols, vals = [], []
        for k, v in campos.items():
            if k == "mezcla":
                v = json.dumps({str(d): n for d, n in v.items()})
            cols.append(f"{k} = ?")
            vals.append(v)
        return ", ".join(cols), vals

    def actualizar(self, tx_id: str, **campos: Any) -> None:
        if not campos:
            return
        sets, vals = self._set(campos | {"actualizado": _ahora()})
        with self.db.transaccion() as c:
            c.execute(f"UPDATE transacciones SET {sets} WHERE id = ?", (*vals, tx_id))

    def transicion(self, tx_id: str, nuevo: Estado, detalle: str | None = None, **campos: Any) -> None:
        with self.db.transaccion() as c:
            fila = c.execute("SELECT estado FROM transacciones WHERE id = ?", (tx_id,)).fetchone()
            if fila is None:
                raise TransicionInvalida(f"no existe la transacción {tx_id}")
            actual = Estado(fila["estado"])
            if nuevo not in TRANSICIONES.get(actual, frozenset()):
                raise TransicionInvalida(f"{tx_id}: {actual} -> {nuevo} no está permitida")
            sets, vals = self._set(campos | {"estado": nuevo.value, "actualizado": _ahora()})
            c.execute(f"UPDATE transacciones SET {sets} WHERE id = ?", (*vals, tx_id))
            c.execute(
                "INSERT INTO historial (tx_id, de, a, detalle, momento) VALUES (?, ?, ?, ?, ?)",
                (tx_id, actual.value, nuevo.value, detalle, _ahora()),
            )

    def pendientes(self, terminal: str) -> list[dict[str, Any]]:
        marcas = ",".join("?" * len(FINALES))
        filas = self.db.consultar(
            f"SELECT * FROM transacciones WHERE terminal = ? AND estado NOT IN ({marcas}) ORDER BY creado",
            (terminal, *[e.value for e in FINALES]),
        )
        return [_fila(f) for f in filas]  # type: ignore[misc]

    def listar(self, terminal: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
        if terminal:
            filas = self.db.consultar(
                "SELECT * FROM transacciones WHERE terminal = ? ORDER BY rowid DESC LIMIT ?", (terminal, limite)
            )
        else:
            filas = self.db.consultar("SELECT * FROM transacciones ORDER BY rowid DESC LIMIT ?", (limite,))
        return [_fila(f) for f in filas]  # type: ignore[misc]

    def historial(self, tx_id: str) -> list[dict[str, Any]]:
        return [dict(f) for f in self.db.consultar("SELECT de, a, detalle, momento FROM historial WHERE tx_id = ? ORDER BY id", (tx_id,))]

    def conteo_por_estado(self, terminal: str | None = None) -> dict[str, int]:
        if terminal:
            filas = self.db.consultar("SELECT estado, COUNT(*) AS n FROM transacciones WHERE terminal = ? GROUP BY estado", (terminal,))
        else:
            filas = self.db.consultar("SELECT estado, COUNT(*) AS n FROM transacciones GROUP BY estado")
        return {f["estado"]: int(f["n"]) for f in filas}

    def aclaraciones(self, terminal: str) -> list[dict[str, Any]]:
        filas = self.db.consultar(
            "SELECT id, tipo, enmascarado, aclaracion, estado, creado FROM transacciones "
            "WHERE terminal = ? AND aclaracion > 0 ORDER BY creado",
            (terminal,),
        )
        return [dict(f) for f in filas]

    # -- STAN ----------------------------------------------------------------------
    def siguiente_stan(self, terminal: str) -> str:
        with self.db.transaccion() as c:
            fila = c.execute("SELECT stan FROM secuencias WHERE terminal = ?", (terminal,)).fetchone()
            n = (int(fila["stan"]) % 999_999) + 1 if fila else 1
            c.execute(
                "INSERT INTO secuencias (terminal, stan) VALUES (?, ?) "
                "ON CONFLICT(terminal) DO UPDATE SET stan = excluded.stan",
                (terminal, n),
            )
        return f"{n:06d}"

    # -- reversas (almacenar y reenviar) -------------------------------------------
    def encolar_reversa(self, tx_id: str, reemplazo: int, motivo_iso: str, **campos_tx: Any) -> None:
        """Encola la 0420 y pasa la transacción a EN_REVERSA en un solo paso atómico."""
        with self.db.transaccion() as c:
            tx = self.obtener(tx_id)
            if tx is None or not tx["stan"]:
                raise TransicionInvalida(f"{tx_id}: no hay 0200 que revertir")
            c.execute(
                "INSERT INTO reversas (tx_id, terminal, tipo, monto, reemplazo, stan, f7, rrn, motivo, creada) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tx_id, tx["terminal"], tx["tipo"], tx["monto"], reemplazo, tx["stan"], tx["f7"], tx["rrn"], motivo_iso, _ahora()),
            )
            self.transicion(tx_id, Estado.EN_REVERSA, detalle=f"motivo ISO {motivo_iso}", **campos_tx)

    def reversa_de(self, tx_id: str) -> dict[str, Any] | None:
        r = self.db.uno("SELECT * FROM reversas WHERE tx_id = ?", (tx_id,))
        return dict(r) if r else None

    def reversas_pendientes(self, terminal: str | None = None, hasta: float | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM reversas WHERE estado = 'PENDIENTE'"
        params: list[Any] = []
        if terminal:
            sql += " AND terminal = ?"
            params.append(terminal)
        if hasta is not None:
            sql += " AND proximo_intento <= ?"
            params.append(hasta)
        return [dict(f) for f in self.db.consultar(sql + " ORDER BY id", tuple(params))]

    def registrar_intento_reversa(self, rev_id: int, error: str, proximo: float) -> None:
        with self.db.transaccion() as c:
            c.execute(
                "UPDATE reversas SET intentos = intentos + 1, ultimo_error = ?, proximo_intento = ? WHERE id = ?",
                (error, proximo, rev_id),
            )

    def acusar_reversa(self, rev_id: int) -> bool:
        """Marca la reversa como acusada. ``False`` si ya lo estaba."""
        with self.db.transaccion() as c:
            cur = c.execute(
                "UPDATE reversas SET estado = 'ACUSADA', intentos = intentos + 1, acusada = ? "
                "WHERE id = ? AND estado = 'PENDIENTE'",
                (_ahora(), rev_id),
            )
            return cur.rowcount == 1
