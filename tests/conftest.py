"""Fixtures communes. Aucun test ne touche le réseau : pytest-socket (--disable-socket) le bloque
et respx intercepte tous les appels httpx."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog

from jobwatch.config import AppConfig, load_config
from jobwatch.digest import ScoredOffer
from jobwatch.models import Offer
from jobwatch.scoring import Reason, ScoreResult

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_scored(
    offer_id: str = "A",
    score: int = 60,
    zone: str = "Paris",
    reasons: tuple[Reason, ...] = (),
    **fields: Any,
) -> ScoredOffer:
    raw = {"id": offer_id, "intitule": f"Poste {offer_id}", "typeContrat": "CDI", **fields}
    return ScoredOffer(Offer.from_api(raw), zone, ScoreResult(score, reasons))


class FakeTime:
    """Horloge monotone factice : le temps n'avance que via `sleep` (ou `advance`)."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


_ENV_VARS = (
    "FT_CLIENT_ID",
    "FT_CLIENT_SECRET",
    "DISCORD_WEBHOOK_URL",
    "PUSHGATEWAY_URL",
    "STATE_PATH",
    "OUT_DIR",
    "CONFIG_PATH",
    "LOG_LEVEL",
    "LOG_FORMAT",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Environnement vierge et cwd temporaire : le .env du développeur n'est jamais lu."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Un test qui configure les logs vers un flux capturé ne doit pas fuiter sur les suivants."""
    yield
    structlog.reset_defaults()
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    """La configuration réellement livrée dans le dépôt."""
    return load_config(ROOT / "config.yaml")
