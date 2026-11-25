import httpx
import pytest
import respx

from jobwatch.metrics import RunStats, push_metrics, render_prometheus

GATEWAY = "http://gw.test:9091"


def test_render_prometheus_success() -> None:
    stats = RunStats(queries_total=45, offers_notified=3, duration_seconds=12.5, success=True)
    text = render_prometheus(stats, now=1_700_000_000)
    assert text.endswith("\n")
    assert "# TYPE jobwatch_run_queries gauge\njobwatch_run_queries 45\n" in text
    assert "jobwatch_run_offers_notified 3\n" in text
    assert "jobwatch_run_duration_seconds 12.5\n" in text
    assert "jobwatch_run_success 1\n" in text
    assert "jobwatch_last_success_timestamp_seconds 1.7e+09" in text


def test_render_prometheus_failure_has_no_success_timestamp() -> None:
    text = render_prometheus(RunStats(success=False), now=1)
    assert "jobwatch_run_success 0" in text
    assert "jobwatch_last_run_timestamp_seconds 1" in text
    assert "last_success" not in text


def test_summary_keys() -> None:
    assert RunStats(dry_run=True, duration_seconds=1.23456).summary() == {
        "requests": 0,
        "requests_failed": 0,
        "http_requests": 0,
        "offers_fetched": 0,
        "offers_unique": 0,
        "offers_new": 0,
        "offers_selected": 0,
        "offers_notified": 0,
        "offers_purged": 0,
        "duration_s": 1.235,
        "dry_run": True,
        "success": False,
    }


def test_push_metrics(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{GATEWAY}/metrics/job/jobwatch").mock(
        return_value=httpx.Response(200)
    )
    assert push_metrics(f"{GATEWAY}/", RunStats(success=True)) is True
    request = route.calls[0].request
    assert request.headers["Content-Type"] == "text/plain; version=0.0.4"
    assert b"jobwatch_run_success 1" in request.content


@pytest.mark.parametrize(
    "response", [httpx.Response(500), httpx.ConnectError("refused")], ids=["500", "connect"]
)
def test_push_failure_never_raises(
    respx_mock: respx.MockRouter, response: httpx.Response | Exception
) -> None:
    route = respx_mock.post(f"{GATEWAY}/metrics/job/jobwatch")
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    with httpx.Client() as client:
        assert push_metrics(GATEWAY, RunStats(), http=client) is False
