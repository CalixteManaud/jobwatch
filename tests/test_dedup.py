from datetime import UTC, datetime, timedelta

import pytest

from jobwatch.collect import FoundOffer, collect, deduplicate
from jobwatch.config import SearchConfig
from jobwatch.errors import AuthError, SearchError
from jobwatch.francetravail import SearchQuery, SearchResult
from jobwatch.models import Offer

UNTIL = datetime(2026, 11, 25, 5, 30, tzinfo=UTC)
SINCE = UNTIL - timedelta(hours=26)

CONFIG = SearchConfig(
    departments={"75": "Paris", "92": "Paris", "59": "Lille"},
    keywords=["devops", "sre"],
)


def offer(offer_id: str) -> Offer:
    return Offer.from_api({"id": offer_id, "intitule": f"Offre {offer_id}"})


class FakeClient:
    def __init__(self, answers: dict[tuple[str, str], list[str] | Exception]) -> None:
        self.answers = answers
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> SearchResult:
        self.queries.append(query)
        answer = self.answers.get((query.keywords, query.department or ""), [])
        if isinstance(answer, Exception):
            raise answer
        return SearchResult(query=query, offers=[offer(i) for i in answer], total=len(answer))


def test_deduplicate_keeps_first_occurrence() -> None:
    items = [
        FoundOffer(offer("A"), "Paris", "75"),
        FoundOffer(offer("B"), "Paris", "92"),
        FoundOffer(offer("A"), "Lille", "59"),
    ]
    assert [(f.offer.id, f.zone) for f in deduplicate(items)] == [("A", "Paris"), ("B", "Paris")]


def test_collect_runs_one_query_per_keyword_and_department() -> None:
    client = FakeClient({})
    result = collect(client, CONFIG, since=SINCE, until=UNTIL)
    assert [(q.keywords, q.department) for q in client.queries] == [
        ("devops", "75"),
        ("devops", "92"),
        ("devops", "59"),
        ("sre", "75"),
        ("sre", "92"),
        ("sre", "59"),
    ]
    assert all(q.since == SINCE and q.until == UNTIL for q in client.queries)
    assert all(q.contract_type == "CDI" for q in client.queries)
    assert result.queries_total == 6
    assert result.offers == []


def test_collect_deduplicates_globally_and_maps_zones() -> None:
    client = FakeClient(
        {
            ("devops", "75"): ["A", "B"],
            ("devops", "92"): ["B", "C"],
            ("sre", "75"): ["A"],
            ("sre", "59"): ["D"],
        }
    )
    result = collect(client, CONFIG, since=SINCE, until=UNTIL)
    assert result.fetched == 6
    assert [(f.offer.id, f.zone, f.department) for f in result.offers] == [
        ("A", "Paris", "75"),
        ("B", "Paris", "75"),
        ("C", "Paris", "92"),
        ("D", "Lille", "59"),
    ]


def test_partial_failure_does_not_stop_the_run() -> None:
    client = FakeClient({("devops", "75"): SearchError("boom"), ("sre", "59"): ["X"]})
    result = collect(client, CONFIG, since=SINCE, until=UNTIL)
    assert result.queries_failed == 1
    assert not result.all_failed
    assert [f.offer.id for f in result.offers] == ["X"]


def test_all_queries_failed_is_reported() -> None:
    client = FakeClient(
        {(kw, dept): SearchError("down") for kw in CONFIG.keywords for dept in CONFIG.departments}
    )
    result = collect(client, CONFIG, since=SINCE, until=UNTIL)
    assert result.queries_failed == result.queries_total == 6
    assert result.all_failed


def test_auth_error_aborts_collection() -> None:
    client = FakeClient({("devops", "75"): AuthError("refusé")})
    with pytest.raises(AuthError):
        collect(client, CONFIG, since=SINCE, until=UNTIL)
    assert len(client.queries) == 1
