"""Statistiques d'un run : résumé loggé et, si PUSHGATEWAY_URL est défini, push Prometheus."""

import time
from dataclasses import dataclass
from typing import Any

import httpx

from jobwatch import __version__
from jobwatch.log import get_logger

log = get_logger(__name__)

_GAUGES: tuple[tuple[str, str, str], ...] = (
    ("queries_total", "jobwatch_run_queries", "Requêtes de recherche exécutées"),
    ("queries_failed", "jobwatch_run_queries_failed", "Requêtes de recherche en échec"),
    ("http_requests", "jobwatch_run_http_requests", "Appels HTTP émis vers France Travail"),
    ("offers_fetched", "jobwatch_run_offers_fetched", "Offres reçues, doublons compris"),
    ("offers_unique", "jobwatch_run_offers_unique", "Offres uniques analysées"),
    ("offers_new", "jobwatch_run_offers_new", "Offres jamais vues auparavant"),
    ("offers_selected", "jobwatch_run_offers_selected", "Offres retenues pour le digest"),
    ("offers_notified", "jobwatch_run_offers_notified", "Offres envoyées sur Discord"),
    ("offers_purged", "jobwatch_run_offers_purged", "Offres supprimées par la rétention"),
    ("duration_seconds", "jobwatch_run_duration_seconds", "Durée du run en secondes"),
)


@dataclass(slots=True)
class RunStats:
    dry_run: bool = False
    queries_total: int = 0
    queries_failed: int = 0
    http_requests: int = 0
    offers_fetched: int = 0
    offers_unique: int = 0
    offers_new: int = 0
    offers_selected: int = 0
    offers_notified: int = 0
    offers_purged: int = 0
    duration_seconds: float = 0.0
    success: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "requests": self.queries_total,
            "requests_failed": self.queries_failed,
            "http_requests": self.http_requests,
            "offers_fetched": self.offers_fetched,
            "offers_unique": self.offers_unique,
            "offers_new": self.offers_new,
            "offers_selected": self.offers_selected,
            "offers_notified": self.offers_notified,
            "offers_purged": self.offers_purged,
            "duration_s": round(self.duration_seconds, 3),
            "dry_run": self.dry_run,
            "success": self.success,
        }


def render_prometheus(stats: RunStats, *, now: float | None = None) -> str:
    """Format texte d'exposition Prometheus (gauges)."""
    timestamp = now if now is not None else time.time()
    samples: list[tuple[str, str, float]] = [
        (name, help_text, float(getattr(stats, attr))) for attr, name, help_text in _GAUGES
    ]
    samples.append(("jobwatch_run_success", "1 si le run a abouti, 0 sinon", float(stats.success)))
    samples.append(("jobwatch_last_run_timestamp_seconds", "Fin du dernier run (epoch)", timestamp))
    if stats.success:
        # absent en cas d'échec : la Pushgateway conserve alors la valeur précédente (POST)
        samples.append(
            ("jobwatch_last_success_timestamp_seconds", "Fin du dernier run réussi", timestamp)
        )
    lines: list[str] = []
    for name, help_text, value in samples:
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name} {value:g}"]
    return "\n".join(lines) + "\n"


def push_metrics(
    gateway_url: str,
    stats: RunStats,
    *,
    job: str = "jobwatch",
    http: httpx.Client | None = None,
) -> bool:
    """Pousse les métriques ; un échec est loggé mais n'échoue jamais le run."""
    endpoint = f"{gateway_url.rstrip('/')}/metrics/job/{job}"
    client = http or httpx.Client(
        timeout=httpx.Timeout(10.0), headers={"User-Agent": f"jobwatch/{__version__}"}
    )
    try:
        # POST : ne remplace que les métriques de même nom dans le groupe
        response = client.post(
            endpoint,
            content=render_prometheus(stats),
            headers={"Content-Type": "text/plain; version=0.0.4"},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.warning("metrics.push_failed", status=exc.response.status_code)
        return False
    except httpx.TransportError as exc:
        log.warning("metrics.push_failed", error=type(exc).__name__)
        return False
    finally:
        if http is None:
            client.close()
    log.info("metrics.pushed", job=job)
    return True
