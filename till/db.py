"""Acceso a SQLite compartido por la bitácora y el libro mayor.

La bitácora de transacciones y el libro mayor viven en el mismo archivo para
que un cambio de estado y su asiento contable se confirmen en una sola
transacción atómica. Modo WAL y ``synchronous=FULL``: si el proceso muere, lo
confirmado sobrevive y al reiniciar se retoma lo pendiente.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class BaseDatos:
    def __init__(self, ruta: str | Path, sincrono: str = "FULL"):
        """``sincrono``: FULL (por omisión) sobrevive a un corte del sistema operativo;
        NORMAL sólo a la muerte del proceso (suficiente para simulaciones)."""
        if sincrono not in ("FULL", "NORMAL"):
            raise ValueError("sincrono debe ser FULL o NORMAL")
        self.ruta = str(ruta)
        if self.ruta != ":memory:":
            Path(self.ruta).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: nosotros controlamos BEGIN/COMMIT explícitamente.
        self.conn = sqlite3.connect(self.ruta, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(f"PRAGMA synchronous={sincrono}")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._profundidad = 0
        self._candado = threading.RLock()

    @contextmanager
    def transaccion(self) -> Iterator[sqlite3.Connection]:
        """Transacción anidable: sólo la más externa hace BEGIN/COMMIT."""
        with self._candado:
            externa = self._profundidad == 0
            if externa:
                self.conn.execute("BEGIN IMMEDIATE")
            self._profundidad += 1
            try:
                yield self.conn
            except BaseException:
                self._profundidad -= 1
                if externa:
                    self.conn.execute("ROLLBACK")
                raise
            else:
                self._profundidad -= 1
                if externa:
                    self.conn.execute("COMMIT")

    def crear_esquema(self, script: str) -> None:
        """``executescript`` confirma lo pendiente: sólo se usa al arrancar."""
        with self._candado:
            if self._profundidad:
                raise RuntimeError("no se puede crear el esquema dentro de una transacción")
            self.conn.executescript(script)

    def ejecutar(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._candado:
            return self.conn.execute(sql, params)

    def consultar(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._candado:
            return self.conn.execute(sql, params).fetchall()

    def uno(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._candado:
            return self.conn.execute(sql, params).fetchone()

    def cerrar(self) -> None:
        with self._candado:
            self.conn.close()
