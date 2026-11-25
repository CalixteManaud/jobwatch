import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from jobwatch.digest import Digest, ScoredOffer
from jobwatch.discord import (
    MAX_EMBED_CHARS_PER_MESSAGE,
    MAX_EMBEDS_PER_MESSAGE,
    MAX_FIELD_VALUE,
    MAX_TITLE,
    DiscordMessage,
    DiscordNotifier,
    build_embed,
    build_messages,
    embed_size,
)
from jobwatch.errors import NotifyError
from jobwatch.scoring import Reason

from .conftest import FakeTime, make_scored

WEBHOOK = "https://discord.com/api/webhooks/123/secret-token"
NOW = datetime(2026, 11, 25, 5, 30, tzinfo=UTC)


def digest(items: list[ScoredOffer], analysed: int = 42) -> Digest:
    return Digest(generated_at=NOW, window="26h", min_score=40, analysed=analysed, items=items)


# --------------------------------------------------------------------------- formatage


def test_nothing_new_sends_short_message() -> None:
    messages = build_messages(digest([], analysed=87))
    assert len(messages) == 1
    payload = messages[0].payload
    assert "rien de neuf" in payload["content"]
    assert "87 offres analysées" in payload["content"]
    assert "embeds" not in payload
    assert messages[0].offer_ids == ()


def test_embed_content() -> None:
    item = make_scored(
        "200AAAA",
        score=95,
        intitule="Ingénieur DevOps Junior H/F",
        entreprise={"nom": "NUAGE CONSEIL"},
        lieuTravail={"libelle": "75 - Paris 15e Arrondissement"},
        salaire={"libelle": "Annuel de 40k à 45k"},
        dateCreation="2026-11-24T09:12:44Z",
        reasons=(
            Reason(4, "stack : aws"),
            Reason(20, "débutant accepté"),
            Reason(15, "signal junior : junior"),
            Reason(-8, "séniorité (description) : senior"),
        ),
    )
    embed = build_embed(item)
    assert embed["title"] == "🟢 Ingénieur DevOps Junior H/F"
    assert embed["url"] == "https://candidat.francetravail.fr/offres/recherche/detail/200AAAA"
    assert embed["color"] == 0x2ECC71
    assert embed["timestamp"] == "2026-11-24T09:12:44+00:00"
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Entreprise"] == "NUAGE CONSEIL"
    assert fields["Ville"] == "Paris 15e Arrondissement"
    assert fields["Salaire"] == "Annuel de 40k à 45k"
    # 3 raisons les plus fortes seulement
    assert fields["Score 95/100 · priorité 1"] == (
        "• débutant accepté (+20)\n• signal junior : junior (+15)\n"
        "• séniorité (description) : senior (-8)"
    )
    assert embed["footer"]["text"] == "France Travail 200AAAA · 24/11/2026"


def test_embed_without_salary_or_company() -> None:
    embed = build_embed(make_scored("B", score=45))
    names = [field["name"] for field in embed["fields"]]
    assert "Salaire" not in names
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Entreprise"] == "Entreprise non communiquée"
    assert fields["Ville"] == "Paris"  # repli sur la zone
    assert fields["Score 45/100 · priorité 3"] == "—"
    assert "timestamp" not in embed


def test_embed_fields_are_truncated() -> None:
    item = make_scored(
        intitule="x" * 500,
        entreprise={"nom": "y" * 2000},
        reasons=(Reason(10, "z" * 3000),),
    )
    embed = build_embed(item)
    assert len(embed["title"]) == MAX_TITLE
    assert embed["title"].endswith("…")
    assert all(len(field["value"]) <= MAX_FIELD_VALUE for field in embed["fields"])


def test_messages_grouped_by_city_best_first() -> None:
    items = [
        make_scored("L1", 55, "Lille"),
        make_scored("P1", 90, "Paris"),
        make_scored("P2", 45, "Paris"),
        make_scored("Y1", 70, "Lyon"),
    ]
    messages = build_messages(digest(items))
    assert "**4 nouvelles offres**" in messages[0].payload["content"]
    assert "Paris 2 · Lyon 1 · Lille 1" in messages[0].payload["content"]
    contents = [m.payload["content"] for m in messages[1:]]
    assert contents == [
        "📍 **Paris** · 2 offres",
        "📍 **Lyon** · 1 offre",
        "📍 **Lille** · 1 offre",
    ]
    assert [m.offer_ids for m in messages[1:]] == [("P1", "P2"), ("Y1",), ("L1",)]
    assert all(m.payload["allowed_mentions"] == {"parse": []} for m in messages)


def test_split_at_ten_embeds() -> None:
    items = [make_scored(f"P{i:02d}", 60, "Paris") for i in range(25)]
    messages = build_messages(digest(items))[1:]
    assert [len(m.payload["embeds"]) for m in messages] == [10, 10, 5]
    assert messages[1].payload["content"] == "📍 **Paris** (suite)"
    assert sum(len(m.offer_ids) for m in messages) == 25


def test_split_at_6000_characters() -> None:
    long_reasons = tuple(Reason(-i, "r" * 1000) for i in range(1, 4))
    items = [
        make_scored(f"P{i}", 60, "Paris", reasons=long_reasons, intitule="t" * 250)
        for i in range(8)
    ]
    messages = build_messages(digest(items))[1:]
    assert len(messages) > 1
    for message in messages:
        embeds = message.payload["embeds"]
        assert len(embeds) <= MAX_EMBEDS_PER_MESSAGE
        assert sum(embed_size(e) for e in embeds) <= MAX_EMBED_CHARS_PER_MESSAGE
    assert sum(len(m.offer_ids) for m in messages) == 8


# --------------------------------------------------------------------------- envoi


def make_notifier(fake_time: FakeTime) -> DiscordNotifier:
    return DiscordNotifier(WEBHOOK, sleep=fake_time.sleep, clock=fake_time.clock)


def test_send_posts_each_message(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.post(WEBHOOK).mock(return_value=httpx.Response(200, json={"id": "1"}))
    messages = build_messages(digest([make_scored("A"), make_scored("B", zone="Lyon")]))
    notifier = make_notifier(fake_time)
    assert notifier.send(messages) == ["A", "B"]
    notifier.close()
    assert route.call_count == 3
    request = route.calls[1].request
    assert request.url.params["wait"] == "true"
    assert json.loads(request.content) == messages[1].payload


def test_send_respects_discord_retry_after(
    respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    respx_mock.post(WEBHOOK).mock(
        side_effect=[
            httpx.Response(429, json={"message": "rate limited", "retry_after": 1.5}),
            httpx.Response(204),
        ]
    )
    make_notifier(fake_time).send([DiscordMessage({"content": "x"})])
    assert 1.5 in fake_time.sleeps


def test_send_retry_after_header_fallback(
    respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    respx_mock.post(WEBHOOK).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(204)]
    )
    make_notifier(fake_time).send([DiscordMessage({"content": "x"})])
    assert 3.0 in fake_time.sleeps


def test_rejected_message_reports_what_was_sent(
    respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    respx_mock.post(WEBHOOK).mock(
        side_effect=[
            httpx.Response(204),
            httpx.Response(204),
            httpx.Response(400, json={"code": 50035, "message": "Invalid Form Body"}),
        ]
    )
    messages = [
        DiscordMessage({"content": "header"}),
        DiscordMessage({"content": "Paris"}, ("A", "B")),
        DiscordMessage({"content": "Lyon"}, ("C",)),
    ]
    with pytest.raises(NotifyError, match="HTTP 400") as exc:
        make_notifier(fake_time).send(messages)
    assert exc.value.sent_ids == ["A", "B"]
    assert exc.value.exit_code == 4
    assert "secret-token" not in str(exc.value)


def test_discord_down_after_retries(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.post(WEBHOOK).mock(return_value=httpx.Response(502, text="bad gateway"))
    with pytest.raises(NotifyError, match="indisponible") as exc:
        make_notifier(fake_time).send([DiscordMessage({"content": "x"}, ("A",))])
    assert route.call_count == 5
    assert exc.value.sent_ids == []


def test_discord_unreachable(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    respx_mock.post(WEBHOOK).mock(side_effect=httpx.ConnectError(f"cannot reach {WEBHOOK}"))
    with pytest.raises(NotifyError, match="ConnectError") as exc:
        make_notifier(fake_time).send([DiscordMessage({"content": "x"})])
    assert "secret-token" not in str(exc.value)
