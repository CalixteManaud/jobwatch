"""Fixtures communes. Aucun test ne touche le réseau : pytest-socket (--disable-socket) le bloque
et respx intercepte tous les appels httpx."""

from pathlib import Path

import pytest

from jobwatch.config import AppConfig, load_config

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

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


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    """La configuration réellement livrée dans le dépôt."""
    return load_config(ROOT / "config.yaml")
