"""Orchestration d'un run : collecte → scoring → état → sorties → notification."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from jobwatch.collect import SearchClient, collect
from jobwatch.config import AppConfig
from jobwatch.digest import Digest, ScoredOffer
from jobwatch.discord import DiscordMessage, build_messages
from jobwatch.errors import AllQueriesFailedError, NotifyError
from jobwatch.exporters import write_csv, write_markdown
from jobwatch.log import get_logger
from jobwatch.metrics import RunStats
from jobwatch.scoring import Scorer
from jobwatch.store import OfferRow, OfferStore

log = get_logger(__name__)


class ApiClient(SearchClient, Protocol):
    @property
    def http_requests(self) -> int: ...

    def authenticate(self) -> None: ...


class Notifier(Protocol):
    def send(self, messages: list[DiscordMessage]) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class RunOptions:
    window: timedelta
    window_label: str
    dry_run: bool = False
    notify: bool = True
    min_score: int | None = None


@dataclass(frozen=True, slots=True)
class RunReport:
    digest: Digest
    csv_path: Path
    markdown_path: Path


def run_pipeline(
    *,
    config: AppConfig,
    client: ApiClient,
    store: OfferStore,
    notifier: Notifier | None,
    out_dir: Path,
    options: RunOptions,
    stats: RunStats,
    now: datetime,
) -> RunReport:
    """Exécute un run complet et met `stats` à jour au fil de l'eau (même en cas d'erreur).

    - `dry_run` : vrais appels de lecture à l'API, fichiers dans `out_dir`, mais ni écriture
      d'état ni Discord.
    - `notify=False` : état et fichiers mis à jour, pas de Discord ; les offres ne sont pas
      marquées notifiées et partiront au prochain run normal.
    """
    stats.dry_run = options.dry_run
    client.authenticate()

    collected = collect(client, config.search, since=now - options.window, until=now)
    stats.queries_total = collected.queries_total
    stats.queries_failed = collected.queries_failed
    stats.offers_fetched = collected.fetched
    stats.offers_unique = len(collected.offers)
    stats.http_requests = client.http_requests
    if collected.all_failed:
        raise AllQueriesFailedError(
            f"Les {collected.queries_total} requêtes de recherche ont échoué."
        )

    scorer = Scorer(config.scoring, contract_type=config.search.contract_type)
    scored = [ScoredOffer(f.offer, f.zone, scorer.score(f.offer)) for f in collected.offers]
    ids = [item.offer.id for item in scored]
    already_notified = store.notified_ids(ids)
    stats.offers_new = len(set(ids) - store.known_ids(ids))
    if not store.readonly:
        store.upsert_many(
            (OfferRow(item.offer.id, item.score, item.offer.payload) for item in scored),
            seen_at=now,
        )
        stats.offers_purged = _purge(store, config, options, now)

    threshold = options.min_score if options.min_score is not None else config.notify.min_score
    selected = [
        item
        for item in scored
        if not item.result.excluded
        and item.score >= threshold
        and item.offer.id not in already_notified
    ]
    stats.offers_selected = len(selected)
    digest = Digest(
        generated_at=now,
        window=options.window_label,
        min_score=threshold,
        analysed=len(scored),
        items=selected,
    )
    report = RunReport(digest, write_csv(digest, out_dir), write_markdown(digest, out_dir))
    log.info("run.outputs", csv=str(report.csv_path), markdown=str(report.markdown_path))

    if options.notify and not options.dry_run:
        if notifier is None:  # pragma: no cover - garanti par la CLI
            raise RuntimeError("notifier requis quand notify=True")
        _notify(notifier, store, digest, stats)
    else:
        log.info("discord.skipped", reason="dry-run" if options.dry_run else "--no-notify")

    stats.success = True
    return report


def _purge(store: OfferStore, config: AppConfig, options: RunOptions, now: datetime) -> int:
    """Purge les offres vues il y a plus de `state.retention_days` jours.

    La rétention effective n'est jamais plus courte que la fenêtre de recherche : une offre
    encore dans la fenêtre (donc vue depuis moins longtemps) reste connue et une offre déjà
    notifiée ne peut pas repartir.
    """
    retention = max(timedelta(days=config.state.retention_days), options.window)
    purged = store.purge_seen_before(now - retention)
    if purged:
        log.info("state.purged", offers=purged, retention_days=retention.days)
    return purged


def _notify(notifier: Notifier, store: OfferStore, digest: Digest, stats: RunStats) -> None:
    try:
        sent = notifier.send(build_messages(digest))
    except NotifyError as exc:
        # ce qui est parti ne doit jamais repartir, même si la suite a échoué
        store.mark_notified(exc.sent_ids)
        stats.offers_notified = len(exc.sent_ids)
        raise
    store.mark_notified(sent)
    stats.offers_notified = len(sent)
