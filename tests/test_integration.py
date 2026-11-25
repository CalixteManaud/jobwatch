"""Run complet mocké : token + 2 pages (206) + 204, vérification de toutes les sorties."""

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from jobwatch.cli import app
from jobwatch.store import OfferStore

from .helpers import WEBHOOK, log_event, mock_france_travail, read_csv_rows, setup_env

runner = CliRunner()


def test_full_run_then_nothing_new(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = setup_env(monkeypatch, tmp_path)
    auth, search = mock_france_travail(respx_mock)
    discord = respx_mock.post(WEBHOOK).mock(return_value=httpx.Response(204))

    # ---------------------------------------------------------------- 1er run
    result = runner.invoke(app, ["run", "--since", "26h"])
    assert result.exit_code == 0, result.output

    # France Travail : 1 token, Paris en 2 pages, Lille en 204
    assert auth.call_count == 1
    calls = [
        (c.request.url.params["departement"], c.request.url.params["range"]) for c in search.calls
    ]
    assert calls == [("75", "0-149"), ("75", "2-151"), ("59", "0-149")]

    # Discord : en-tête + un message « Paris » avec les 2 offres retenues, triées par score
    assert discord.call_count == 2
    header, paris = (json.loads(call.request.content) for call in discord.calls)
    assert "**2 nouvelles offres**" in header["content"]
    assert paris["content"] == "📍 **Paris** · 2 offres"
    assert [embed["title"] for embed in paris["embeds"]] == [
        "🟢 Ingénieur DevOps Junior H/F",
        "⚪ Ingénieur de production (H/F)",
    ]
    assert "secret" not in result.stderr

    # CSV : senior (score 0) et alternance (exclue) absents
    rows = read_csv_rows(paths["out"])
    assert [(r["Priorité"], r["Entreprise"], r["Poste à viser"]) for r in rows] == [
        ("1", "NUAGE CONSEIL", "Ingénieur DevOps Junior H/F"),
        ("3", "Entreprise non communiquée", "Ingénieur de production (H/F)"),
    ]
    assert rows[1]["Source"] == "https://candidat.francetravail.fr/offres/recherche/detail/200DDDD"
    assert rows[1]["Contact à chercher sur LinkedIn"] == "« Talent Acquisition » Paris"

    # Markdown
    (markdown,) = paths["out"].glob("digest-*.md")
    assert "## 📍 Paris (2)" in markdown.read_text(encoding="utf-8")

    # État : 4 offres connues, 2 notifiées
    with OfferStore.open(paths["state"], readonly=True) as store:
        assert store.count() == 4
        assert store.notified_ids(["200AAAA", "200BBBB", "200CCCC", "200DDDD"]) == {
            "200AAAA",
            "200DDDD",
        }

    summary = log_event(result.stderr, "run.summary")
    assert summary | {"duration_s": 0} == {
        "event": "run.summary",
        "level": "info",
        "timestamp": summary["timestamp"],
        "requests": 2,
        "requests_failed": 0,
        "http_requests": 4,
        "offers_fetched": 4,
        "offers_unique": 4,
        "offers_new": 4,
        "offers_selected": 2,
        "offers_notified": 2,
        "duration_s": 0,
        "dry_run": False,
        "success": True,
    }

    # ---------------------------------------------------------------- 2e run : rien ne repart
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0, result.output
    assert discord.call_count == 3
    assert "rien de neuf" in json.loads(discord.calls[-1].request.content)["content"]
    summary = log_event(result.stderr, "run.summary")
    assert (summary["offers_new"], summary["offers_selected"], summary["offers_notified"]) == (
        0,
        0,
        0,
    )
    assert read_csv_rows(paths["out"]) == []
