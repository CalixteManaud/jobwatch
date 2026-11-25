from pathlib import Path

import pytest

from jobwatch.errors import ConfigError
from jobwatch.settings import Settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.state_path == Path("state/jobwatch.db")
    assert settings.out_dir == Path("out")
    assert settings.config_path == Path("config.yaml")
    assert settings.log_format == "json"
    assert settings.pushgateway_url is None


def test_reads_dotenv_from_cwd(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "FT_CLIENT_ID=id-123\nFT_CLIENT_SECRET=s3cr3t\nSTATE_PATH=/data/state/x.db\n",
        encoding="utf-8",
    )
    settings = Settings()
    assert settings.ft_credentials() == ("id-123", "s3cr3t")
    assert settings.state_path == Path("/data/state/x.db")


def test_blank_values_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FT_CLIENT_ID", "")
    monkeypatch.setenv("FT_CLIENT_SECRET", "   ")
    monkeypatch.setenv("PUSHGATEWAY_URL", "")
    settings = Settings()
    assert settings.ft_client_id is None
    assert settings.pushgateway_url is None
    with pytest.raises(ConfigError, match="FT_CLIENT_ID"):
        settings.ft_credentials()


def test_secrets_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FT_CLIENT_SECRET", "s3cr3t")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
    settings = Settings()
    assert "s3cr3t" not in repr(settings)
    assert "tok" not in str(settings.discord_webhook_url)


def test_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="DISCORD_WEBHOOK_URL"):
        Settings().webhook_url()
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "http://insecure")
    with pytest.raises(ConfigError, match="https"):
        Settings().webhook_url()
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/tok")
    assert Settings().webhook_url().endswith("/1/tok")


def test_log_level_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert Settings().log_level == "DEBUG"
