"""Outils partagés par les tests de la CLI (environnement complet + mocks HTTP)."""

import csv
import io
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml

from jobwatch.francetravail import AUTH_URL, SEARCH_URL

from .conftest import ROOT, load_json

WEBHOOK = "https://discord.com/api/webhooks/42/discord-secret"
PUSHGATEWAY = "http://pushgateway.test:9091"


def setup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **overrides: str) -> dict[str, Path]:
    """Environnement de run complet dans tmp_path, avec une config réduite à 2 requêtes."""
    shipped = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    shipped["search"]["keywords"] = ["devops"]
    shipped["search"]["departments"] = {"75": "Paris", "59": "Lille"}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(shipped, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    paths = {
        "config": config_path,
        "state": tmp_path / "state" / "jobwatch.db",
        "out": tmp_path / "out",
    }
    env = {
        "FT_CLIENT_ID": "client-id",
        "FT_CLIENT_SECRET": "client-secret",
        "DISCORD_WEBHOOK_URL": WEBHOOK,
        "STATE_PATH": str(paths["state"]),
        "OUT_DIR": str(paths["out"]),
        "CONFIG_PATH": str(config_path),
        "LOG_FORMAT": "json",
        "COLUMNS": "220",
        **overrides,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # throttling et backoff sans attente réelle
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    return paths


def ft_page_handler(request: httpx.Request) -> httpx.Response:
    """Paris : 2 pages (206 puis 206 final) ; autres départements : 204."""
    if request.url.params.get("departement") != "75":
        return httpx.Response(204)
    if request.url.params["range"].startswith("0-"):
        return httpx.Response(
            206, json=load_json("ft_search_page1.json"), headers={"Content-Range": "offres 0-1/4"}
        )
    return httpx.Response(
        206, json=load_json("ft_search_page2.json"), headers={"Content-Range": "offres 2-3/4"}
    )


def mock_france_travail(respx_mock: respx.MockRouter) -> tuple[respx.Route, respx.Route]:
    auth = respx_mock.post(AUTH_URL).mock(
        return_value=httpx.Response(200, json=load_json("ft_token.json"))
    )
    search = respx_mock.get(SEARCH_URL).mock(side_effect=ft_page_handler)
    return auth, search


def json_logs(stderr: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stderr.splitlines() if line.startswith("{")]


def log_event(stderr: str, event: str) -> dict[str, Any]:
    matches = [record for record in json_logs(stderr) if record["event"] == event]
    assert matches, f"événement {event!r} absent des logs"
    return matches[-1]


def read_csv_rows(out_dir: Path) -> list[dict[str, str]]:
    (path,) = out_dir.glob("jobwatch-*.csv")
    text = path.read_bytes().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=";"))
