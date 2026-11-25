"""Logs structurés : JSON sur stderr par défaut (lisible par Loki / CloudWatch...), console en dev.

Les données métier (tableaux rich, digest) passent par stdout ; les logs par stderr.
"""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO

import structlog
from structlog.typing import FilteringBoundLogger, Processor

REDACTED = "***"
_SENSITIVE_KEYS = frozenset(
    {"authorization", "access_token", "client_secret", "password", "secret", "token", "webhook_url"}
)
_WEBHOOK_RE = re.compile(r"https://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\S+")


def redact_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Processor structlog : masque les clés sensibles et toute URL de webhook Discord."""
    for key, value in list(event_dict.items()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = REDACTED
        elif isinstance(value, str) and "webhooks/" in value:
            event_dict[key] = _WEBHOOK_RE.sub(f"https://discord.com/api/webhooks/{REDACTED}", value)
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json", stream: TextIO | None = None) -> None:
    out = stream if stream is not None else sys.stderr
    numeric = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    logging.basicConfig(level=numeric, stream=out, format="%(message)s", force=True)
    # httpx logue chaque URL en INFO : l'URL du webhook Discord contient un secret.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_secrets,
    ]
    if fmt == "console":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors += [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.PrintLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> FilteringBoundLogger:
    logger: FilteringBoundLogger = structlog.get_logger(name)
    return logger
