"""Paramètres d'exécution lus depuis l'environnement (et un éventuel fichier `.env`).

Les secrets sont typés `SecretStr` : ils n'apparaissent jamais dans un `repr`, un log ou une trace.
"""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from jobwatch.errors import ConfigError
from jobwatch.francetravail import AUTH_URL

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ft_client_id: SecretStr | None = None
    ft_client_secret: SecretStr | None = None
    discord_webhook_url: SecretStr | None = None
    pushgateway_url: str | None = None
    # endpoint OAuth2 du royaume partenaire (non secret), surchargeable sans toucher au code
    ft_auth_url: str = AUTH_URL

    state_path: Path = Path("state/jobwatch.db")
    out_dir: Path = Path("out")
    config_path: Path = Path("config.yaml")

    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    @field_validator(
        "ft_client_id", "ft_client_secret", "discord_webhook_url", "pushgateway_url", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """`FOO=` dans un .env ou un Secret vaut « non défini »."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("ft_auth_url", mode="before")
    @classmethod
    def _auth_url_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return AUTH_URL
        return value

    @field_validator("ft_auth_url")
    @classmethod
    def _auth_url_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("FT_AUTH_URL doit être une URL https://")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    def ft_credentials(self) -> tuple[str, str]:
        """Identifiants France Travail, ou `ConfigError` s'ils manquent."""
        if self.ft_client_id is None or self.ft_client_secret is None:
            raise ConfigError("FT_CLIENT_ID et FT_CLIENT_SECRET sont requis (voir .env.example).")
        return self.ft_client_id.get_secret_value(), self.ft_client_secret.get_secret_value()

    def webhook_url(self) -> str:
        """URL du webhook Discord, ou `ConfigError` si elle manque."""
        if self.discord_webhook_url is None:
            raise ConfigError(
                "DISCORD_WEBHOOK_URL est requis pour notifier "
                "(sinon utiliser --no-notify ou --dry-run)."
            )
        url = self.discord_webhook_url.get_secret_value()
        if not url.startswith("https://"):
            raise ConfigError("DISCORD_WEBHOOK_URL doit être une URL https://.")
        return url
