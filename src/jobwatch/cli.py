"""Interface en ligne de commande (typer) : `run`, `search`, `sample`."""

import json
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from jobwatch import __version__
from jobwatch.anonymize import anonymize_offers, field_presence
from jobwatch.config import load_config
from jobwatch.digest import ScoredOffer, format_date, sort_by_score
from jobwatch.discord import DiscordNotifier
from jobwatch.errors import JobwatchError
from jobwatch.francetravail import FranceTravailClient, SearchQuery
from jobwatch.log import configure_logging, get_logger
from jobwatch.metrics import RunStats, push_metrics
from jobwatch.models import Offer
from jobwatch.pipeline import RunOptions, RunReport, run_pipeline
from jobwatch.scoring import Scorer
from jobwatch.settings import Settings
from jobwatch.store import OfferStore

app = typer.Typer(
    name="jobwatch",
    help="Veille quotidienne des offres France Travail (CDI DevOps / Cloud / Run).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
log = get_logger("jobwatch.cli")

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([mhdjw])\s*$", re.IGNORECASE)
_WINDOW_UNITS = {"m": "minutes", "h": "hours", "d": "days", "j": "days", "w": "weeks"}
MAX_WINDOW = timedelta(days=90)
_PRIORITY_STYLES = {1: "bold green", 2: "yellow", 3: "dim"}

SinceOption = Annotated[
    str, typer.Option("--since", help="Fenêtre de création des offres : 26h, 7d, 2w, 90m.")
]
ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Chemin du config.yaml (défaut : variable CONFIG_PATH)."),
]


# --------------------------------------------------------------------------- utilitaires


def parse_window(value: str) -> timedelta:
    """`26h` → 26 heures ; unités : m, h, d (ou j), w."""
    match = _WINDOW_RE.match(value)
    if match is None:
        raise typer.BadParameter("format attendu : 26h, 7d, 2w ou 90m", param_hint="--since")
    amount, unit = int(match.group(1)), match.group(2).lower()
    window = timedelta(**{_WINDOW_UNITS[unit]: amount})
    if not timedelta(0) < window <= MAX_WINDOW:
        raise typer.BadParameter("fenêtre entre 1 minute et 90 jours", param_hint="--since")
    return window


def _bootstrap() -> Settings:
    try:
        settings = Settings()
    except ValidationError as exc:
        typer.echo(f"Variables d'environnement invalides :\n{exc}", err=True)
        raise typer.Exit(2) from exc
    configure_logging(settings.log_level, settings.log_format)
    return settings


@contextmanager
def _handle_errors(command: str) -> Iterator[None]:
    try:
        yield
    except JobwatchError as exc:
        log.error(
            f"{command}.failed",
            error=str(exc),
            error_type=type(exc).__name__,
            exit_code=exc.exit_code,
        )
        raise typer.Exit(exc.exit_code) from exc


def _offers_table(title: str, items: list[ScoredOffer]) -> Table:
    table = Table(title=escape(title), title_justify="left", expand=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("P", justify="center", no_wrap=True)
    table.add_column("Poste", ratio=3)
    table.add_column("Entreprise", ratio=2)
    table.add_column("Ville", ratio=2)
    table.add_column("Publiée", no_wrap=True)
    table.add_column("Raisons", ratio=3)
    for item in items:
        offer = item.offer
        excluded = item.result.excluded
        table.add_row(
            "exclue" if excluded else str(item.score),
            "-" if excluded else str(item.priority),
            f"[link={offer.url}]{escape(offer.intitule or offer.id)}[/link]",
            escape(offer.company),
            escape(item.city),
            format_date(offer.date_creation),
            escape(", ".join(str(reason) for reason in item.result.top_reasons(2))),
            style="red" if excluded else _PRIORITY_STYLES[item.priority],
        )
    return table


def _print_dry_run(report: RunReport) -> None:
    console = Console()
    digest = report.digest
    title = f"[dry-run] {len(digest.items)} offre(s) seraient envoyées (seuil {digest.min_score})"
    console.print(_offers_table(title, digest.ranked()))
    console.print(f"CSV      : {report.csv_path}")
    console.print(f"Markdown : {report.markdown_path}")


# --------------------------------------------------------------------------- commandes


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"jobwatch {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Affiche la version."
        ),
    ] = False,
) -> None:
    """Veille quotidienne des offres France Travail (CDI DevOps / Cloud / Run)."""


@app.command()
def run(
    since: SinceOption = "26h",
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Appels API réels, digest affiché et fichiers écrits, mais ni état ni Discord.",
        ),
    ] = False,
    notify: Annotated[
        bool,
        typer.Option(
            "--notify/--no-notify",
            help="Envoyer le digest sur Discord (--no-notify : envoi au prochain run).",
        ),
    ] = True,
    min_score: Annotated[
        int | None,
        typer.Option("--min-score", min=0, max=100, help="Seuil d'envoi (défaut : config.yaml)."),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Collecte les nouvelles offres, les score et envoie le digest (premier run : --since 7d)."""
    window = parse_window(since)
    settings = _bootstrap()
    stats = RunStats(dry_run=dry_run)
    started = time.monotonic()
    report: RunReport | None = None
    exit_code = 0
    log.info("run.start", version=__version__, since=since, dry_run=dry_run, notify=notify)
    try:
        app_config = load_config(config or settings.config_path)
        client_id, client_secret = settings.ft_credentials()
        notifier = DiscordNotifier(settings.webhook_url()) if notify and not dry_run else None
        try:
            with (
                FranceTravailClient(client_id, client_secret) as client,
                OfferStore.open(settings.state_path, readonly=dry_run) as store,
            ):
                try:
                    report = run_pipeline(
                        config=app_config,
                        client=client,
                        store=store,
                        notifier=notifier,
                        out_dir=settings.out_dir,
                        options=RunOptions(window, since, dry_run, notify, min_score),
                        stats=stats,
                        now=datetime.now(UTC),
                    )
                finally:
                    stats.http_requests = client.http_requests
        finally:
            if notifier is not None:
                notifier.close()
    except JobwatchError as exc:
        exit_code = exc.exit_code
        log.error("run.failed", error=str(exc), error_type=type(exc).__name__, exit_code=exit_code)
    finally:
        stats.duration_seconds = time.monotonic() - started
        log.info("run.summary", **stats.summary())
        if settings.pushgateway_url:
            push_metrics(settings.pushgateway_url, stats)

    if report is not None and dry_run:
        _print_dry_run(report)
    if exit_code:
        raise typer.Exit(exit_code)


@app.command()
def search(
    keywords: Annotated[str, typer.Argument(help="Mots-clés, par exemple « kubernetes ».")],
    dept: Annotated[str | None, typer.Option("--dept", help="Code département, ex. 59.")] = None,
    since: SinceOption = "7d",
    limit: Annotated[int, typer.Option(min=1, max=500, help="Nombre de lignes affichées.")] = 30,
    config: ConfigOption = None,
) -> None:
    """Recherche ponctuelle, offres scorées affichées en tableau (aucun état modifié)."""
    window = parse_window(since)
    settings = _bootstrap()
    with _handle_errors("search"):
        app_config = load_config(config or settings.config_path)
        client_id, client_secret = settings.ft_credentials()
        now = datetime.now(UTC)
        search_config = app_config.search
        zone = search_config.zone(dept) if dept else "France"
        query = SearchQuery(keywords, dept, now - window, now, search_config.contract_type)
        with FranceTravailClient(client_id, client_secret) as client:
            client.authenticate()
            result = client.search(query)
    scorer = Scorer(app_config.scoring, contract_type=search_config.contract_type)
    items = sort_by_score([ScoredOffer(o, zone, scorer.score(o)) for o in result.offers])
    title = (
        f"{len(items)} offre(s) {search_config.contract_type} pour {keywords!r} ({zone}, {since})"
    )
    if len(items) > limit:
        title += f" : {limit} premières"
    Console().print(_offers_table(title, items[:limit]))


@app.command()
def sample(
    keywords: Annotated[str, typer.Option("--keywords", "-k", help="Mots-clés.")] = "devops",
    dept: Annotated[str, typer.Option("--dept", help="Code département.")] = "75",
    count: Annotated[int, typer.Option(min=1, max=150, help="Nombre d'offres.")] = 5,
    since: SinceOption = "7d",
    output: Annotated[Path, typer.Option("--output", "-o", help="Fichier JSON produit.")] = Path(
        "tests/fixtures/ft_sample.json"
    ),
) -> None:
    """Enregistre une vraie réponse de l'API, anonymisée, comme fixture de test."""
    window = parse_window(since)
    settings = _bootstrap()
    with _handle_errors("sample"):
        client_id, client_secret = settings.ft_credentials()
        now = datetime.now(UTC)
        with FranceTravailClient(client_id, client_secret) as client:
            raw = client.fetch_raw(SearchQuery(keywords, dept, now - window, now), count)

    offers = anonymize_offers(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"resultats": offers}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    console = Console()
    console.print(f"{len(offers)} offre(s) anonymisée(s) enregistrée(s) dans {output}")
    table = Table(title="Champs attendus par les modèles", title_justify="left")
    table.add_column("Champ")
    table.add_column("Renseigné", justify="right")
    for field_name, present in field_presence(offers).items():
        style = "green" if present == len(offers) else ("yellow" if present else "red")
        table.add_row(field_name, f"{present}/{len(offers)}", style=style)
    console.print(table)

    invalid = 0
    for raw_offer in offers:
        try:
            Offer.from_api(raw_offer)
        except ValidationError as exc:
            invalid += 1
            console.print(f"[red]Offre invalide {raw_offer.get('id')!r}[/red] : {exc}")
    modelled = {info.alias or name for name, info in Offer.model_fields.items()}
    unmodelled = sorted({key for raw_offer in offers for key in raw_offer} - modelled)
    console.print(f"Offres valides : {len(offers) - invalid}/{len(offers)}")
    console.print(f"Champs non modélisés (ignorés) : {', '.join(unmodelled) or 'aucun'}")
