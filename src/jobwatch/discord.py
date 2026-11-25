"""Digest Discord via webhook : un message d'en-tête puis un message par ville, en embeds.

Limites Discord respectées : 10 embeds et 6000 caractères (cumulés) par message, plus les
limites par champ. L'URL du webhook est un secret : elle n'apparaît dans aucun log ni erreur.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from jobwatch import __version__
from jobwatch.digest import Digest, ScoredOffer, format_date, plural
from jobwatch.errors import NotifyError
from jobwatch.log import get_logger
from jobwatch.ratelimit import RateLimiter
from jobwatch.retry import (
    RETRYABLE_STATUS,
    RetryableResponseError,
    build_retrying,
    parse_retry_after,
)

MAX_EMBEDS_PER_MESSAGE = 10
MAX_EMBED_CHARS_PER_MESSAGE = 6000
MAX_CONTENT = 2000
MAX_TITLE = 256
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_AUTHOR = 256

PRIORITY_COLORS = {1: 0x2ECC71, 2: 0xF1C40F, 3: 0x95A5A6}
PRIORITY_ICONS = {1: "🟢", 2: "🟡", 3: "⚪"}

log = get_logger(__name__)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class DiscordMessage:
    payload: dict[str, Any]
    offer_ids: tuple[str, ...] = ()


def embed_size(embed: dict[str, Any]) -> int:
    """Caractères comptés par Discord dans la limite des 6000 par message."""
    size = len(embed.get("title", "")) + len(embed.get("description", ""))
    size += len(embed.get("footer", {}).get("text", ""))
    size += len(embed.get("author", {}).get("name", ""))
    size += sum(len(f["name"]) + len(f["value"]) for f in embed.get("fields", []))
    return size


def build_embed(item: ScoredOffer) -> dict[str, Any]:
    offer = item.offer
    fields: list[dict[str, Any]] = [
        {"name": "Entreprise", "value": truncate(offer.company, MAX_FIELD_VALUE), "inline": True},
        {"name": "Ville", "value": truncate(item.city, MAX_FIELD_VALUE), "inline": True},
    ]
    if offer.salary:
        fields.append(
            {"name": "Salaire", "value": truncate(offer.salary, MAX_FIELD_VALUE), "inline": True}
        )
    reasons = "\n".join(f"• {reason}" for reason in item.result.top_reasons(3)) or "—"
    fields.append(
        {
            "name": truncate(f"Score {item.score}/100 · priorité {item.priority}", MAX_FIELD_NAME),
            "value": truncate(reasons, MAX_FIELD_VALUE),
            "inline": False,
        }
    )
    footer = " · ".join(
        part for part in (f"France Travail {offer.id}", format_date(offer.date_creation)) if part
    )
    embed: dict[str, Any] = {
        "title": truncate(
            f"{PRIORITY_ICONS[item.priority]} {offer.intitule or 'Offre'}", MAX_TITLE
        ),
        "url": offer.url,
        "color": PRIORITY_COLORS[item.priority],
        "fields": fields,
        "footer": {"text": truncate(footer, MAX_FOOTER)},
    }
    if offer.date_creation is not None:
        embed["timestamp"] = offer.date_creation.isoformat()
    return embed


def chunk_embeds(embeds: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Découpe en paquets d'au plus 10 embeds et 6000 caractères."""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 0
    for embed in embeds:
        size = embed_size(embed)
        full = len(current) >= MAX_EMBEDS_PER_MESSAGE
        if current and (full or current_size + size > MAX_EMBED_CHARS_PER_MESSAGE):
            chunks.append(current)
            current, current_size = [], 0
        current.append(embed)
        current_size += size
    if current:
        chunks.append(current)
    return chunks


def _message(content: str, embeds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "username": "jobwatch",
        "content": truncate(content, MAX_CONTENT),
        "allowed_mentions": {"parse": []},  # jamais de @everyone venu d'un titre d'offre
    }
    if embeds:
        payload["embeds"] = embeds
    return payload


def build_messages(digest: Digest) -> list[DiscordMessage]:
    if not digest.items:
        return [
            DiscordMessage(
                _message(
                    f"😴 **jobwatch** · {digest.run_date_label} : rien de neuf "
                    f"(fenêtre {digest.window}, {plural(digest.analysed, 'offre analysée')}, "
                    f"seuil {digest.min_score})."
                )
            )
        ]

    groups = digest.groups()
    summary = " · ".join(f"{zone} {len(items)}" for zone, items in groups)
    header = (
        f"🆕 **jobwatch** · {digest.run_date_label} : "
        f"**{plural(len(digest.items), 'nouvelle offre')}** "
        f"(fenêtre {digest.window}, seuil {digest.min_score}, "
        f"{digest.analysed} analysées)\n{summary}"
    )
    messages = [DiscordMessage(_message(header))]
    for zone, items in groups:
        embeds = [build_embed(item) for item in items]
        offset = 0
        for index, chunk in enumerate(chunk_embeds(embeds)):
            ids = tuple(item.offer.id for item in items[offset : offset + len(chunk)])
            offset += len(chunk)
            if index == 0:
                title = f"📍 **{zone}** · {plural(len(items), 'offre')}"
            else:
                title = f"📍 **{zone}** (suite)"
            messages.append(DiscordMessage(_message(title, chunk), ids))
    return messages


class DiscordNotifier:
    def __init__(
        self,
        webhook_url: str,
        *,
        http: httpx.Client | None = None,
        max_attempts: int = 5,
        rate_per_second: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = webhook_url
        self._owns_http = http is None
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(15.0), headers={"User-Agent": f"jobwatch/{__version__}"}
        )
        self._limiter = RateLimiter(rate_per_second, clock=clock, sleep=sleep)
        self._retrying = build_retrying(target="discord", max_attempts=max_attempts, sleep=sleep)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def send(self, messages: list[DiscordMessage]) -> list[str]:
        """Envoie les messages dans l'ordre ; renvoie les ids des offres effectivement envoyées.

        En cas d'échec, `NotifyError.sent_ids` liste ce qui est déjà parti (pour ne pas le
        renvoyer au run suivant).
        """
        sent: list[str] = []
        total = len(messages)
        for index, message in enumerate(messages, start=1):
            try:
                response = self._retrying.copy()(self._post, message.payload)
            except RetryableResponseError as exc:
                raise NotifyError(
                    f"Discord indisponible (message {index}/{total}, {exc})", sent_ids=sent
                ) from exc
            except httpx.TransportError as exc:
                raise NotifyError(
                    f"Discord injoignable (message {index}/{total}, {type(exc).__name__})",
                    sent_ids=sent,
                ) from exc
            if response.status_code >= 400:
                raise NotifyError(
                    f"Discord a refusé le message {index}/{total} "
                    f"(HTTP {response.status_code}) : {response.text[:200]}",
                    sent_ids=sent,
                )
            sent.extend(message.offer_ids)
        log.info("discord.sent", messages=total, offers=len(sent))
        return sent

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        self._limiter.acquire()
        response = self._http.post(self._url, params={"wait": "true"}, json=payload)
        if response.status_code in RETRYABLE_STATUS:
            raise RetryableResponseError(response.status_code, _discord_retry_after(response))
        return response


def _discord_retry_after(response: httpx.Response) -> float | None:
    """Discord renvoie `retry_after` (secondes, décimal) dans le corps JSON d'un 429."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("retry_after"), int | float):
        return max(float(body["retry_after"]), 0.0)
    return parse_retry_after(response.headers.get("Retry-After"))
