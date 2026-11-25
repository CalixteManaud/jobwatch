import io
import json

import pytest

from jobwatch.log import REDACTED, configure_logging, get_logger, redact_secrets


def test_redact_sensitive_keys_and_webhooks() -> None:
    event = redact_secrets(
        None,
        "info",
        {
            "event": "x",
            "Authorization": "Bearer abc",
            "client_secret": "s3cr3t",
            "detail": "POST https://discord.com/api/webhooks/123/very-secret failed",
            "count": 3,
        },
    )
    assert event["Authorization"] == REDACTED
    assert event["client_secret"] == REDACTED
    assert "very-secret" not in event["detail"]
    assert event["detail"].startswith("POST https://discord.com/api/webhooks/***")
    assert event["count"] == 3


def test_json_logs_on_given_stream() -> None:
    stream = io.StringIO()
    configure_logging("INFO", "json", stream=stream)
    log = get_logger("test")
    log.debug("filtré")
    log.info("run.summary", offers_new=2, token="abc", ville="Lyon é")

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(lines) == 1
    record = lines[0]
    assert record["event"] == "run.summary"
    assert record["level"] == "info"
    assert record["offers_new"] == 2
    assert record["token"] == REDACTED
    assert record["ville"] == "Lyon é"
    assert record["timestamp"].endswith("Z")


def test_json_logs_render_exceptions() -> None:
    stream = io.StringIO()
    configure_logging("INFO", "json", stream=stream)
    log = get_logger("test")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed")
    record = json.loads(stream.getvalue().splitlines()[0])
    assert record["exception"][0]["exc_type"] == "ValueError"


def test_console_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("debug", "console")
    get_logger("test").debug("hello", key="value")
    err = capsys.readouterr().err
    assert "hello" in err
    assert "value" in err
