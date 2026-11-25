"""Collecte : une requête par couple (mot-clé, département), dédoublonnage global par id.

Un échec de requête est loggé et n'arrête pas le run ; une erreur d'authentification, si.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from jobwatch.config import SearchConfig
from jobwatch.errors import SearchError
from jobwatch.francetravail import SearchQuery, SearchResult
from jobwatch.log import get_logger
from jobwatch.models import Offer

log = get_logger(__name__)


class SearchClient(Protocol):
    def search(self, query: SearchQuery) -> SearchResult: ...


@dataclass(frozen=True, slots=True)
class FoundOffer:
    offer: Offer
    zone: str
    department: str


@dataclass(slots=True)
class CollectResult:
    offers: list[FoundOffer] = field(default_factory=list)
    fetched: int = 0
    queries_total: int = 0
    queries_failed: int = 0
    truncated_queries: int = 0

    @property
    def all_failed(self) -> bool:
        return self.queries_total > 0 and self.queries_failed == self.queries_total


def deduplicate(items: Iterable[FoundOffer]) -> list[FoundOffer]:
    """Garde la première occurrence de chaque id (et donc la zone de la première requête)."""
    unique: dict[str, FoundOffer] = {}
    for item in items:
        unique.setdefault(item.offer.id, item)
    return list(unique.values())


def collect(
    client: SearchClient, config: SearchConfig, *, since: datetime, until: datetime
) -> CollectResult:
    result = CollectResult()
    found: list[FoundOffer] = []
    for keywords, department in config.queries():
        result.queries_total += 1
        query = SearchQuery(keywords, department, since, until, config.contract_type)
        try:
            search = client.search(query)
        except SearchError as exc:
            result.queries_failed += 1
            log.warning("search.failed", query=query.label, error=str(exc))
            continue
        result.fetched += len(search.offers)
        result.truncated_queries += int(search.truncated)
        zone = config.zone(department)
        found.extend(FoundOffer(offer, zone, department) for offer in search.offers)
    result.offers = deduplicate(found)
    return result
