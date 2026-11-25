"""État persistant en SQLite : une offre déjà notifiée ne repart jamais.

Table `offers(id, first_seen, score, notified, payload)`. Le score et le payload sont mis à jour
à chaque run (la config peut changer) ; `first_seen` et `notified` ne bougent jamais à l'upsert.
"""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from jobwatch.errors import ConfigError

SCHEMA_VERSION = 1
_SQLITE_MAX_VARIABLES = 500  # marge sous la limite historique de 999 paramètres

_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id         TEXT    PRIMARY KEY,
    first_seen TEXT    NOT NULL,
    score      INTEGER NOT NULL,
    notified   INTEGER NOT NULL DEFAULT 0 CHECK (notified IN (0, 1)),
    payload    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offers_notified ON offers (notified);
"""

_UPSERT = """
INSERT INTO offers (id, first_seen, score, notified, payload) VALUES (?, ?, ?, 0, ?)
ON CONFLICT (id) DO UPDATE SET score = excluded.score, payload = excluded.payload
"""


@dataclass(frozen=True, slots=True)
class StoredOffer:
    id: str
    first_seen: datetime
    score: int
    notified: bool
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OfferRow:
    id: str
    score: int
    payload: dict[str, Any]


def _chunks(values: list[str], size: int = _SQLITE_MAX_VARIABLES) -> Iterator[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


class OfferStore:
    def __init__(self, connection: sqlite3.Connection, *, readonly: bool = False) -> None:
        self._conn = connection
        self._readonly = readonly

    # -- ouverture
    @classmethod
    def open(cls, path: Path, *, readonly: bool = False) -> Self:
        """Ouvre (et crée au besoin) la base.

        En lecture seule (`--dry-run`), rien n'est jamais écrit : une base absente est remplacée
        par une base vide en mémoire, une base existante est ouverte en `mode=ro`.
        """
        if readonly:
            if not path.exists():
                return cls.in_memory(readonly=True)
            connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            store = cls(connection, readonly=True)
            store._check_version()
            return store
        path.parent.mkdir(parents=True, exist_ok=True)
        store = cls(sqlite3.connect(path, timeout=10))
        store._init_schema()
        return store

    @classmethod
    def in_memory(cls, *, readonly: bool = False) -> Self:
        store = cls(sqlite3.connect(":memory:"), readonly=readonly)
        store._init_schema()
        return store

    def _init_schema(self) -> None:
        self._check_version()
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _check_version(self) -> None:
        (version,) = self._conn.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            raise ConfigError(
                f"Base d'état en version {version}, cette version de jobwatch gère "
                f"la version {SCHEMA_VERSION} : mettre jobwatch à jour."
            )

    # -- cycle de vie
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    @property
    def readonly(self) -> bool:
        return self._readonly

    # -- lecture
    def _ids_where(self, ids: Iterable[str], condition: str) -> set[str]:
        found: set[str] = set()
        for chunk in _chunks(list(dict.fromkeys(ids))):
            placeholders = ",".join("?" * len(chunk))
            sql = f"SELECT id FROM offers WHERE id IN ({placeholders}){condition}"  # noqa: S608
            found.update(row[0] for row in self._conn.execute(sql, chunk))
        return found

    def known_ids(self, ids: Iterable[str]) -> set[str]:
        return self._ids_where(ids, "")

    def notified_ids(self, ids: Iterable[str]) -> set[str]:
        return self._ids_where(ids, " AND notified = 1")

    def get(self, offer_id: str) -> StoredOffer | None:
        row = self._conn.execute(
            "SELECT id, first_seen, score, notified, payload FROM offers WHERE id = ?",
            (offer_id,),
        ).fetchone()
        if row is None:
            return None
        return StoredOffer(
            id=row[0],
            first_seen=datetime.fromisoformat(row[1]),
            score=row[2],
            notified=bool(row[3]),
            payload=json.loads(row[4]),
        )

    def count(self, *, notified: bool | None = None) -> int:
        if notified is None:
            (total,) = self._conn.execute("SELECT COUNT(*) FROM offers").fetchone()
        else:
            (total,) = self._conn.execute(
                "SELECT COUNT(*) FROM offers WHERE notified = ?", (int(notified),)
            ).fetchone()
        return int(total)

    # -- écriture
    def _ensure_writable(self) -> None:
        if self._readonly:
            raise RuntimeError("base ouverte en lecture seule (dry-run)")

    def upsert_many(self, rows: Iterable[OfferRow], *, seen_at: datetime) -> set[str]:
        """Insère ou met à jour les offres ; renvoie les ids qui n'étaient pas encore connus."""
        self._ensure_writable()
        rows = list(rows)
        known = self.known_ids(row.id for row in rows)
        first_seen = seen_at.isoformat(timespec="seconds")
        with self._conn:
            self._conn.executemany(
                _UPSERT,
                [
                    (
                        row.id,
                        first_seen,
                        row.score,
                        json.dumps(row.payload, ensure_ascii=False, separators=(",", ":")),
                    )
                    for row in rows
                ],
            )
        return {row.id for row in rows} - known

    def mark_notified(self, ids: Iterable[str]) -> int:
        self._ensure_writable()
        updated = 0
        with self._conn:
            for chunk in _chunks(list(dict.fromkeys(ids))):
                placeholders = ",".join("?" * len(chunk))
                cursor = self._conn.execute(
                    f"UPDATE offers SET notified = 1 WHERE id IN ({placeholders})",  # noqa: S608
                    chunk,
                )
                updated += cursor.rowcount
        return updated
