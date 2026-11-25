import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jobwatch.errors import ConfigError
from jobwatch.store import OfferRow, OfferStore

T0 = datetime(2026, 11, 24, 5, 30, tzinfo=UTC)
T1 = T0 + timedelta(days=1)


def row(offer_id: str, score: int = 50, **payload: object) -> OfferRow:
    return OfferRow(offer_id, score, {"id": offer_id, **payload})


def test_open_creates_parent_dir_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "state" / "sub" / "jobwatch.db"
    with OfferStore.open(path) as store:
        assert store.count() == 0
    assert path.exists()
    with sqlite3.connect(path) as raw:
        columns = [info[1] for info in raw.execute("PRAGMA table_info(offers)")]
        assert columns == ["id", "first_seen", "score", "notified", "payload"]
        assert raw.execute("PRAGMA user_version").fetchone() == (1,)


def test_upsert_returns_only_new_ids_and_keeps_first_seen() -> None:
    with OfferStore.in_memory() as store:
        assert store.upsert_many([row("A", 40), row("B")], seen_at=T0) == {"A", "B"}
        assert store.upsert_many([row("A", 90, intitule="v2"), row("C")], seen_at=T1) == {"C"}

        stored = store.get("A")
        assert stored is not None
        assert stored.score == 90  # score recalculé
        assert stored.payload == {"id": "A", "intitule": "v2"}
        assert stored.first_seen == T0  # première vue inchangée
        assert stored.notified is False
        assert store.count() == 3


def test_notified_is_never_reset() -> None:
    with OfferStore.in_memory() as store:
        store.upsert_many([row("A"), row("B")], seen_at=T0)
        assert store.mark_notified(["A", "A", "inconnu"]) == 1
        store.upsert_many([row("A", 10)], seen_at=T1)

        assert store.notified_ids(["A", "B", "C"]) == {"A"}
        assert store.known_ids(["A", "B", "C"]) == {"A", "B"}
        assert store.count(notified=True) == 1
        assert store.count(notified=False) == 1


def test_state_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "jobwatch.db"
    with OfferStore.open(path) as store:
        store.upsert_many([row("A", payload_text="é")], seen_at=T0)
        store.mark_notified(["A"])
    with OfferStore.open(path) as store:
        stored = store.get("A")
        assert stored is not None
        assert stored.notified is True
        assert stored.payload["payload_text"] == "é"


def test_readonly_missing_file_is_not_created(tmp_path: Path) -> None:
    path = tmp_path / "absent" / "jobwatch.db"
    with OfferStore.open(path, readonly=True) as store:
        assert store.readonly
        assert store.known_ids(["A"]) == set()
        with pytest.raises(RuntimeError, match="lecture seule"):
            store.upsert_many([row("A")], seen_at=T0)
    assert not path.exists()
    assert not path.parent.exists()


def test_readonly_existing_file_reads_but_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "jobwatch.db"
    with OfferStore.open(path) as store:
        store.upsert_many([row("A")], seen_at=T0)
        store.mark_notified(["A"])
    before = path.read_bytes()
    with OfferStore.open(path, readonly=True) as store:
        assert store.notified_ids(["A"]) == {"A"}
        with pytest.raises(RuntimeError):
            store.mark_notified(["A"])
    assert path.read_bytes() == before


def test_many_ids_are_chunked() -> None:
    ids = [f"ID{i}" for i in range(1234)]
    with OfferStore.in_memory() as store:
        assert len(store.upsert_many([row(i) for i in ids], seen_at=T0)) == 1234
        assert store.mark_notified(ids) == 1234
        assert len(store.notified_ids(ids)) == 1234


def test_get_unknown_returns_none() -> None:
    with OfferStore.in_memory() as store:
        assert store.get("nope") is None


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "jobwatch.db"
    with sqlite3.connect(path) as raw:
        raw.execute("PRAGMA user_version = 99")
    with pytest.raises(ConfigError, match="version 99"):
        OfferStore.open(path)
    with pytest.raises(ConfigError, match="version 99"):
        OfferStore.open(path, readonly=True)
