"""Client de l'API France Travail « Offres d'emploi v2 ».

- Token OAuth2 `client_credentials` mis en cache jusqu'à `expires_in - 60 s`, renouvelé sur 401.
- Throttling à 4 req/s (limite API : 10/s) et retry exponentiel sur 429 / 5xx (`Retry-After`).
- Pagination par `range` (150 max par page, début ≤ 1000, fin ≤ 1149) pilotée par `Content-Range` :
  200 = complet, 206 = partiel (page suivante), 204 = aucun résultat.
"""

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import ValidationError
from tenacity import Retrying

from jobwatch import __version__
from jobwatch.errors import AuthError, SearchError
from jobwatch.log import get_logger
from jobwatch.models import Offer
from jobwatch.ratelimit import RateLimiter
from jobwatch.retry import RETRYABLE_STATUS, RetryableResponseError, build_retrying

AUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
AUTH_REALM = "/partenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"

PAGE_SIZE = 150
MAX_RANGE_START = 1000
MAX_RANGE_END = 1149
TOKEN_SAFETY_MARGIN_S = 60.0
DEFAULT_RATE_PER_SECOND = 4.0

_CONTENT_RANGE_RE = re.compile(r"^\s*(?:[A-Za-z]+\s+)?(\d+)\s*-\s*(\d+)\s*/\s*(\d+|\*)\s*$")

log = get_logger(__name__)


# --------------------------------------------------------------------------- utilitaires purs


@dataclass(frozen=True, slots=True)
class ContentRange:
    start: int
    end: int
    total: int | None


def parse_content_range(value: str | None) -> ContentRange | None:
    """`offres 0-149/1234` → ContentRange(0, 149, 1234) ; `None` si absent ou illisible."""
    if not value:
        return None
    match = _CONTENT_RANGE_RE.match(value)
    if match is None:
        return None
    start, end, total = match.groups()
    return ContentRange(int(start), int(end), None if total == "*" else int(total))


def format_api_datetime(moment: datetime) -> str:
    """Format attendu par `minCreationDate` / `maxCreationDate` : ISO 8601 UTC, sans fraction."""
    if moment.tzinfo is None:
        raise ValueError("datetime naïf refusé : préciser le fuseau (UTC)")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error_detail(response: httpx.Response, limit: int = 200) -> str:
    """Message d'erreur lisible sans jamais renvoyer l'URL complète ni de secret."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:limit].strip()
    if isinstance(body, dict):
        parts = [
            str(body[key])
            for key in ("error", "error_description", "message", "codeErreur")
            if body.get(key)
        ]
        if parts:
            return " - ".join(parts)[:limit]
    return str(body)[:limit]


# --------------------------------------------------------------------------- modèles de requête


@dataclass(frozen=True, slots=True)
class SearchQuery:
    keywords: str
    department: str | None
    since: datetime
    until: datetime
    contract_type: str = "CDI"

    @property
    def label(self) -> str:
        return f"{self.keywords!r}@{self.department or 'France'}"

    def params(self) -> dict[str, str]:
        # min et max sont toujours envoyés ensemble (exigence de l'API)
        params = {
            "motsCles": self.keywords,
            "typeContrat": self.contract_type,
            "minCreationDate": format_api_datetime(self.since),
            "maxCreationDate": format_api_datetime(self.until),
            "sort": "1",  # date de création décroissante : pagination stable
        }
        if self.department:
            params["departement"] = self.department
        return params


@dataclass(slots=True)
class Page:
    status_code: int
    results: list[dict[str, Any]]
    content_range: ContentRange | None


@dataclass(slots=True)
class SearchResult:
    query: SearchQuery
    offers: list[Offer] = field(default_factory=list)
    total: int | None = None
    pages: int = 0
    invalid: int = 0

    @property
    def truncated(self) -> bool:
        return self.total is not None and self.total > MAX_RANGE_END + 1


# --------------------------------------------------------------------------- authentification


class TokenProvider:
    def __init__(
        self,
        http: httpx.Client,
        client_id: str,
        client_secret: str,
        *,
        limiter: RateLimiter,
        retrying: Retrying,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._limiter = limiter
        self._retrying = retrying
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self.requests = 0

    def get(self) -> str:
        if self._token is None or self._clock() >= self._expires_at:
            self._token = self._fetch()
        return self._token

    def invalidate(self) -> None:
        self._token = None

    def _post(self) -> httpx.Response:
        self._limiter.acquire()
        self.requests += 1
        response = self._http.post(
            AUTH_URL,
            params={"realm": AUTH_REALM},
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": SCOPE,
            },
        )
        if response.status_code in RETRYABLE_STATUS:
            raise RetryableResponseError.from_response(response)
        return response

    def _fetch(self) -> str:
        try:
            response = self._retrying.copy()(self._post)
        except (RetryableResponseError, httpx.TransportError) as exc:
            raise AuthError(f"Service d'authentification indisponible ({exc})") from exc
        if response.status_code != 200:
            detail = _error_detail(response)
            raise AuthError(f"Authentification refusée (HTTP {response.status_code}) : {detail}")
        try:
            body = response.json()
            token = str(body["access_token"])
            expires_in = float(body.get("expires_in", 0))
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthError("Réponse d'authentification illisible") from exc
        self._expires_at = self._clock() + max(expires_in - TOKEN_SAFETY_MARGIN_S, 0.0)
        log.info("auth.token_obtained", expires_in=expires_in)
        return token


# --------------------------------------------------------------------------- client


class FranceTravailClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.Client | None = None,
        page_size: int = PAGE_SIZE,
        rate_per_second: float = DEFAULT_RATE_PER_SECOND,
        max_attempts: int = 5,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not 1 <= page_size <= PAGE_SIZE:
            raise ValueError(f"page_size doit être entre 1 et {PAGE_SIZE}")
        self._owns_http = http is None
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"User-Agent": f"jobwatch/{__version__}", "Accept": "application/json"},
        )
        self._page_size = page_size
        self._limiter = RateLimiter(rate_per_second, clock=clock, sleep=sleep)
        self._retrying = build_retrying(
            target="francetravail", max_attempts=max_attempts, sleep=sleep
        )
        self._tokens = TokenProvider(
            self._http,
            client_id,
            client_secret,
            limiter=self._limiter,
            retrying=self._retrying,
            clock=clock if clock is not None else time.monotonic,
        )
        self._search_requests = 0

    # -- cycle de vie
    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    @property
    def http_requests(self) -> int:
        """Nombre d'appels HTTP réellement émis (auth + recherche, retries compris)."""
        return self._tokens.requests + self._search_requests

    # -- API publique
    def authenticate(self) -> None:
        """Obtient un token tout de suite : un problème d'identifiants arrête le run au plus tôt."""
        self._tokens.get()

    def search(self, query: SearchQuery) -> SearchResult:
        result = SearchResult(query=query)
        for page in self.iter_pages(query):
            result.pages += 1
            if page.content_range is not None and page.content_range.total is not None:
                result.total = page.content_range.total
            for raw in page.results:
                try:
                    result.offers.append(Offer.from_api(raw))
                except ValidationError as exc:
                    result.invalid += 1
                    log.warning("offer.invalid", query=query.label, errors=exc.error_count())
        if result.total is None:
            result.total = len(result.offers)
        if result.truncated:
            log.warning(
                "search.truncated",
                query=query.label,
                total=result.total,
                fetched=len(result.offers),
                hint="l'API ne pagine pas au-delà de 1150 résultats : affiner la requête",
            )
        log.info(
            "search.done",
            query=query.label,
            total=result.total,
            offers=len(result.offers),
            pages=result.pages,
        )
        return result

    def fetch_raw(self, query: SearchQuery, limit: int) -> list[dict[str, Any]]:
        """Premières offres brutes (JSON d'origine), pour `jobwatch sample`."""
        raw: list[dict[str, Any]] = []
        for page in self.iter_pages(query, max_results=limit):
            raw.extend(page.results)
        return raw[:limit]

    def iter_pages(self, query: SearchQuery, *, max_results: int | None = None) -> Iterator[Page]:
        start = 0
        while True:
            end = min(start + self._page_size - 1, MAX_RANGE_END)
            if max_results is not None:
                end = min(end, max_results - 1)
            page = self._fetch_page(query, start, end)
            yield page

            if page.status_code != 206 or not page.results:
                return  # 200 : tout est là ; 204 : rien
            content_range = page.content_range
            if content_range is None and len(page.results) < end - start + 1:
                return  # pas d'en-tête exploitable et page incomplète : c'était la dernière
            last = content_range.end if content_range else start + len(page.results) - 1
            next_start = last + 1
            total = content_range.total if content_range is not None else None
            if total is not None and next_start >= total:
                return
            if max_results is not None and next_start >= max_results:
                return
            if next_start > MAX_RANGE_END:
                return  # plafond de l'API atteint (signalé par SearchResult.truncated)
            # Au-delà de 1000, la dernière page fenêtrée est 1000-1149 (recouvrement dédoublonné).
            new_start = min(next_start, MAX_RANGE_START)
            if new_start <= start:
                return
            start = new_start

    # -- HTTP
    def _fetch_page(self, query: SearchQuery, start: int, end: int) -> Page:
        params = {**query.params(), "range": f"{start}-{end}"}
        try:
            response = self._get_with_auth(params)
        except (RetryableResponseError, httpx.TransportError) as exc:
            raise SearchError(f"{query.label} : échec après retries ({exc})") from exc

        status = response.status_code
        if status == 204:
            return Page(204, [], None)
        if status not in (200, 206):
            raise SearchError(f"{query.label} : HTTP {status} - {_error_detail(response)}")
        try:
            body = response.json()
        except ValueError as exc:
            raise SearchError(f"{query.label} : réponse JSON illisible") from exc
        results = body.get("resultats") if isinstance(body, dict) else None
        valid = [item for item in results or [] if isinstance(item, dict)]
        return Page(status, valid, parse_content_range(response.headers.get("Content-Range")))

    def _get_with_auth(self, params: dict[str, str]) -> httpx.Response:
        response = self._retrying.copy()(self._send, params, self._tokens.get())
        if response.status_code == 401:
            # token révoqué ou expiré côté serveur : un seul renouvellement
            self._tokens.invalidate()
            response = self._retrying.copy()(self._send, params, self._tokens.get())
        if response.status_code in (401, 403):
            raise AuthError(
                f"Accès refusé par l'API de recherche (HTTP {response.status_code}) : "
                f"{_error_detail(response)}. "
                "L'application est-elle abonnée à « Offres d'emploi v2 » ?"
            )
        return response

    def _send(self, params: dict[str, str], token: str) -> httpx.Response:
        self._limiter.acquire()
        self._search_requests += 1
        response = self._http.get(
            SEARCH_URL, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code in RETRYABLE_STATUS:
            raise RetryableResponseError.from_response(response)
        return response
