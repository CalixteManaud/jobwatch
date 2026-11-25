"""Le digest d'un run : offres retenues, triées par score et groupées par ville (zone)."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, tzinfo
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jobwatch.models import Offer
from jobwatch.scoring import ScoreResult

LOCAL_TZ_NAME = "Europe/Paris"


@cache
def local_tz() -> tzinfo:
    """Fuseau d'affichage ; repli sur UTC si la base tz n'est pas disponible."""
    try:
        return ZoneInfo(LOCAL_TZ_NAME)
    except ZoneInfoNotFoundError:  # pragma: no cover - dépend de l'image système
        return UTC


def format_date(moment: datetime | None) -> str:
    return moment.astimezone(local_tz()).strftime("%d/%m/%Y") if moment else ""


def plural(count: int, word: str) -> str:
    """`plural(2, "nouvelle offre")` → « 2 nouvelles offres »."""
    if count <= 1:
        return f"{count} {word}"
    return f"{count} " + " ".join(f"{part}s" for part in word.split())


@dataclass(frozen=True, slots=True)
class ScoredOffer:
    offer: Offer
    zone: str
    result: ScoreResult

    @property
    def score(self) -> int:
        return self.result.score

    @property
    def priority(self) -> int:
        return self.result.priority

    @property
    def city(self) -> str:
        return self.offer.city or self.zone


def _sort_key(item: ScoredOffer) -> tuple[int, float, str]:
    created = item.offer.date_creation.timestamp() if item.offer.date_creation else 0.0
    return (-item.score, -created, item.offer.id)


def sort_by_score(items: list[ScoredOffer]) -> list[ScoredOffer]:
    """Score décroissant, puis offre la plus récente d'abord."""
    return sorted(items, key=_sort_key)


def group_by_zone(items: list[ScoredOffer]) -> list[tuple[str, list[ScoredOffer]]]:
    """Groupes par zone ; la zone qui porte la meilleure offre vient en premier."""
    groups: dict[str, list[ScoredOffer]] = defaultdict(list)
    for item in sort_by_score(items):
        groups[item.zone].append(item)
    return sorted(groups.items(), key=lambda group: (_sort_key(group[1][0]), group[0]))


@dataclass(frozen=True, slots=True)
class Digest:
    generated_at: datetime
    window: str
    min_score: int
    analysed: int
    items: list[ScoredOffer] = field(default_factory=list)

    @property
    def run_date(self) -> date:
        return self.generated_at.astimezone(local_tz()).date()

    @property
    def run_date_label(self) -> str:
        return self.run_date.strftime("%d/%m/%Y")

    def groups(self) -> list[tuple[str, list[ScoredOffer]]]:
        return group_by_zone(self.items)

    def ranked(self) -> list[ScoredOffer]:
        return sort_by_score(self.items)
