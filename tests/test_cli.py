import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx
import typer
from typer.testing import CliRunner

from jobwatch import __version__
from jobwatch.cli import app, parse_window
from jobwatch.francetravail import AUTH_URL, SEARCH_URL
from jobwatch.store import OfferStore

from .conftest import load_json
from .helpers import (
    PUSHGATEWAY,
    WEBHOOK,
    log_event,
    mock_france_travail,
    read_csv_rows,
    setup_env,
)

runner = CliRunner()
IDS = ["200AAAA", "200BBBB", "200CCCC", "200DDDD"]


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("26h", timedelta(hours=26)),
        ("7d", timedelta(days=7)),
        ("7j", timedelta(days=7)),
        ("2w", timedelta(weeks=2)),
        (" 90M ", timedelta(minutes=90)),
    ],
)
def test_parse_window(value: str, expected: timedelta) -> None:
    assert parse_window(value) == expected


@pytest.mark.parametrize("value", ["", "26", "h", "0h", "91d", "1y", "-2h"])
def test_parse_window_rejects(value: str) -> None:
    with pytest.raises(typer.BadParameter):
        parse_window(value)


def test_invalid_since_is_usage_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    setup_env(monkeypatch, tmp_path)
    result = runner.invoke(app, ["run", "--since", "demain"])
    assert result.exit_code == 2
    assert "format attendu" in result.output


# --------------------------------------------------------------------------- modes de run


def test_dry_run_writes_files_but_no_state_and_no_discord(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = setup_env(monkeypatch, tmp_path, DISCORD_WEBHOOK_URL="")
    mock_france_travail(respx_mock)  # aucune route Discord : un appel ferait échouer le test

    result = runner.invoke(app, ["run", "--dry-run", "--since", "7d"])

    assert result.exit_code == 0, result.output
    assert not paths["state"].exists()
    assert len(read_csv_rows(paths["out"])) == 2
    assert "[dry-run] 2 offre(s) seraient envoyées (seuil 40)" in result.stdout
    assert "Ingénieur DevOps Junior H/F" in result.stdout
    assert log_event(result.stderr, "discord.skipped")["reason"] == "dry-run"
    assert log_event(result.stderr, "run.summary")["dry_run"] is True


def test_no_notify_updates_state_without_marking(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = setup_env(monkeypatch, tmp_path, DISCORD_WEBHOOK_URL="")
    mock_france_travail(respx_mock)

    result = runner.invoke(app, ["run", "--no-notify"])

    assert result.exit_code == 0, result.output
    with OfferStore.open(paths["state"], readonly=True) as store:
        assert store.count() == 4
        assert store.notified_ids(IDS) == set()
    assert log_event(result.stderr, "discord.skipped")["reason"] == "--no-notify"


def test_min_score_override(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = setup_env(monkeypatch, tmp_path)
    mock_france_travail(respx_mock)
    discord = respx_mock.post(WEBHOOK).mock(return_value=httpx.Response(204))

    result = runner.invoke(app, ["run", "--min-score", "90"])

    assert result.exit_code == 0, result.output
    assert [row["Poste à viser"] for row in read_csv_rows(paths["out"])] == [
        "Ingénieur DevOps Junior H/F"
    ]
    assert len(json.loads(discord.calls[1].request.content)["embeds"]) == 1


def test_metrics_pushed_when_gateway_configured(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup_env(monkeypatch, tmp_path, PUSHGATEWAY_URL=PUSHGATEWAY)
    mock_france_travail(respx_mock)
    respx_mock.post(WEBHOOK).mock(return_value=httpx.Response(204))
    gateway = respx_mock.post(f"{PUSHGATEWAY}/metrics/job/jobwatch").mock(
        return_value=httpx.Response(200)
    )

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0, result.output
    body = gateway.calls[0].request.content.decode()
    assert "jobwatch_run_offers_notified 2" in body
    assert "jobwatch_run_success 1" in body


# --------------------------------------------------------------------------- codes de sortie


def test_auth_failure_exits_3(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup_env(monkeypatch, tmp_path)
    respx_mock.post(AUTH_URL).mock(
        return_value=httpx.Response(401, json={"error": "invalid_client"})
    )
    search = respx_mock.get(SEARCH_URL)

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 3
    assert search.call_count == 0
    failed = log_event(result.stderr, "run.failed")
    assert failed["error_type"] == "AuthError"
    assert log_event(result.stderr, "run.summary")["success"] is False


def test_all_queries_failed_exits_1(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup_env(monkeypatch, tmp_path)
    respx_mock.post(AUTH_URL).mock(
        return_value=httpx.Response(200, json=load_json("ft_token.json"))
    )
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(400, json={"message": "KO"}))

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 1
    assert log_event(result.stderr, "run.failed")["error_type"] == "AllQueriesFailedError"
    summary = log_event(result.stderr, "run.summary")
    assert (summary["requests"], summary["requests_failed"]) == (2, 2)


def test_partial_failure_is_logged_and_run_continues(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup_env(monkeypatch, tmp_path)
    respx_mock.post(AUTH_URL).mock(
        return_value=httpx.Response(200, json=load_json("ft_token.json"))
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["departement"] == "75":
            return httpx.Response(503)
        return httpx.Response(204)

    respx_mock.get(SEARCH_URL).mock(side_effect=handler)
    respx_mock.post(WEBHOOK).mock(return_value=httpx.Response(204))

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0, result.output
    assert log_event(result.stderr, "search.failed")["query"] == "'devops'@75"
    assert log_event(result.stderr, "run.summary")["requests_failed"] == 1


def test_discord_failure_exits_4_and_keeps_offers_pending(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = setup_env(monkeypatch, tmp_path)
    mock_france_travail(respx_mock)
    respx_mock.post(WEBHOOK).mock(
        side_effect=[httpx.Response(204), httpx.Response(400, json={"message": "Invalid"})]
    )

    result = runner.invoke(app, ["run"])

    assert result.exit_code == 4
    assert log_event(result.stderr, "run.failed")["error_type"] == "NotifyError"
    with OfferStore.open(paths["state"], readonly=True) as store:
        assert store.count() == 4
        assert store.notified_ids(IDS) == set()  # repartiront au prochain run
    assert len(read_csv_rows(paths["out"])) == 2  # le CSV est quand même produit


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"FT_CLIENT_ID": ""}, "FT_CLIENT_ID"),
        ({"DISCORD_WEBHOOK_URL": ""}, "DISCORD_WEBHOOK_URL"),
        ({"CONFIG_PATH": "/nulle/part.yaml"}, "introuvable"),
    ],
)
def test_configuration_errors_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    setup_env(monkeypatch, tmp_path, **overrides)
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert message in log_event(result.stderr, "run.failed")["error"]


def test_invalid_environment_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    setup_env(monkeypatch, tmp_path, LOG_FORMAT="xml")
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 2
    assert "Variables d'environnement invalides" in result.stderr


# --------------------------------------------------------------------------- search / sample


def test_search_prints_scored_table(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup_env(monkeypatch, tmp_path)
    mock_france_travail(respx_mock)

    result = runner.invoke(app, ["search", "kubernetes", "--dept", "75", "--limit", "3"])

    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "4 offre(s) CDI pour 'kubernetes' (Paris, 7d) : 3 premières" in out
    assert out.index("Ingénieur DevOps Junior H/F") < out.index("Ingénieur de production")
    assert "Architecte Cloud Senior" in out
    assert "Alternance" not in out  # 4e ligne coupée par --limit 3 (exclue, triée en dernier)


def test_search_auth_failure(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup_env(monkeypatch, tmp_path)
    respx_mock.post(AUTH_URL).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    result = runner.invoke(app, ["search", "sre"])
    assert result.exit_code == 3


def test_sample_writes_anonymized_fixture(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup_env(monkeypatch, tmp_path)
    _, search = mock_france_travail(respx_mock)
    output = tmp_path / "fixtures" / "sample.json"

    result = runner.invoke(app, ["sample", "--count", "2", "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert search.calls[0].request.url.params["range"] == "0-1"
    saved = json.loads(output.read_text(encoding="utf-8"))["resultats"]
    assert [offer["id"] for offer in saved] == ["200AAAA", "200BBBB"]
    assert saved[0]["entreprise"]["nom"] == "ENTREPRISE 1"
    assert "NUAGE CONSEIL" not in output.read_text(encoding="utf-8")
    assert "2 offre(s) anonymisée(s)" in result.stdout
    assert "Offres valides : 2/2" in result.stdout
    assert "romeCode" in result.stdout  # champ présent mais non modélisé


def test_auth_url_from_environment(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    setup_env(monkeypatch, tmp_path, FT_AUTH_URL="https://auth.example.test/token")
    custom = respx_mock.post("https://auth.example.test/token").mock(
        return_value=httpx.Response(200, json=load_json("ft_token.json"))
    )
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(204))

    result = runner.invoke(app, ["run", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert custom.call_count == 1
