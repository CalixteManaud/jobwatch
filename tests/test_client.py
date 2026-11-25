from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from jobwatch.errors import AuthError, SearchError
from jobwatch.francetravail import AUTH_URL, SEARCH_URL, FranceTravailClient, SearchQuery

from .conftest import FakeTime, load_json

NOW = datetime(2026, 11, 25, 5, 30, tzinfo=UTC)


def make_query(dept: str | None = "75", keywords: str = "devops") -> SearchQuery:
    return SearchQuery(keywords, dept, NOW - timedelta(hours=26), NOW)


def make_client(fake_time: FakeTime, **kwargs: Any) -> FranceTravailClient:
    return FranceTravailClient(
        "client-id", "client-secret", sleep=fake_time.sleep, clock=fake_time.clock, **kwargs
    )


def page(name: str, content_range: str, status: int = 206) -> httpx.Response:
    return httpx.Response(status, json=load_json(name), headers={"Content-Range": content_range})


@pytest.fixture
def auth_route(respx_mock: respx.MockRouter) -> respx.Route:
    return respx_mock.post(AUTH_URL).mock(
        return_value=httpx.Response(200, json=load_json("ft_token.json"))
    )


# --------------------------------------------------------------------------- authentification


def test_auth_request_and_search_params(
    respx_mock: respx.MockRouter, auth_route: respx.Route, fake_time: FakeTime
) -> None:
    search = respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(204))
    with make_client(fake_time) as client:
        client.search(make_query())
        client.search(make_query("59"))

    assert auth_route.call_count == 1  # token mis en cache
    auth_request = auth_route.calls[0].request
    assert auth_request.url.params["realm"] == "/partenaire"
    assert auth_request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    form = parse_qs(auth_request.content.decode())
    assert form == {
        "grant_type": ["client_credentials"],
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
        "scope": ["api_offresdemploiv2 o2dsoffre"],
    }

    request = search.calls[0].request
    assert request.headers["Authorization"] == "Bearer fake-access-token"
    assert dict(request.url.params) == {
        "motsCles": "devops",
        "departement": "75",
        "typeContrat": "CDI",
        "minCreationDate": "2026-11-24T03:30:00Z",
        "maxCreationDate": "2026-11-25T05:30:00Z",
        "sort": "1",
        "range": "0-149",
    }
    assert search.calls[1].request.url.params["departement"] == "59"


def test_query_without_department(
    respx_mock: respx.MockRouter, auth_route: respx.Route, fake_time: FakeTime
) -> None:
    search = respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(204))
    with make_client(fake_time) as client:
        client.search(make_query(dept=None))
    assert "departement" not in search.calls[0].request.url.params


def test_token_renewed_60s_before_expiry(
    respx_mock: respx.MockRouter, auth_route: respx.Route, fake_time: FakeTime
) -> None:
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(204))
    with make_client(fake_time) as client:
        client.search(make_query())
        fake_time.advance(1499 - 60 - 5)
        client.search(make_query())
        assert auth_route.call_count == 1
        fake_time.advance(10)
        client.search(make_query())
    assert auth_route.call_count == 2


def test_auth_refused(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    respx_mock.post(AUTH_URL).mock(
        return_value=httpx.Response(
            401,
            json={"error": "invalid_client", "error_description": "Client authentication failed"},
        )
    )
    with make_client(fake_time) as client, pytest.raises(AuthError, match="invalid_client") as exc:
        client.authenticate()
    assert exc.value.exit_code == 3
    assert "client-secret" not in str(exc.value)


def test_auth_retried_when_unavailable(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.post(AUTH_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=load_json("ft_token.json"))]
    )
    with make_client(fake_time) as client:
        client.authenticate()
    assert route.call_count == 2


def test_auth_down_after_retries(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    respx_mock.post(AUTH_URL).mock(return_value=httpx.Response(502))
    with (
        make_client(fake_time, max_attempts=2) as client,
        pytest.raises(AuthError, match="indispo"),
    ):
        client.authenticate()


def test_auth_unreadable_response(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    respx_mock.post(AUTH_URL).mock(return_value=httpx.Response(200, json={"oops": 1}))
    with make_client(fake_time) as client, pytest.raises(AuthError, match="illisible"):
        client.authenticate()


# --------------------------------------------------------------------------- statuts & pagination


@pytest.mark.usefixtures("auth_route")
def test_204_means_no_result(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(204))
    with make_client(fake_time) as client:
        result = client.search(make_query())
    assert result.offers == []
    assert result.pages == 1
    assert result.total == 0
    assert result.truncated is False


@pytest.mark.usefixtures("auth_route")
def test_200_is_complete(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=page("ft_search_page1.json", "offres 0-1/2", status=200)
    )
    with make_client(fake_time) as client:
        result = client.search(make_query())
    assert [offer.id for offer in result.offers] == ["200AAAA", "200BBBB"]
    assert result.total == 2
    assert route.call_count == 1


@pytest.mark.usefixtures("auth_route")
def test_206_fetches_next_page(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[
            page("ft_search_page1.json", "offres 0-1/4"),
            page("ft_search_page2.json", "offres 2-3/4"),
        ]
    )
    with make_client(fake_time, page_size=2) as client:
        result = client.search(make_query())
    assert [call.request.url.params["range"] for call in route.calls] == ["0-1", "2-3"]
    assert [offer.id for offer in result.offers] == ["200AAAA", "200BBBB", "200CCCC", "200DDDD"]
    assert result.pages == 2
    assert result.total == 4


@pytest.mark.usefixtures("auth_route")
def test_206_without_content_range_stops_on_short_page(
    respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(206, json={"resultats": [{"id": "1"}, {"id": "2"}]}),
            httpx.Response(206, json={"resultats": [{"id": "3"}]}),
        ]
    )
    with make_client(fake_time, page_size=2) as client:
        result = client.search(make_query())
    assert route.call_count == 2
    assert len(result.offers) == 3


@pytest.mark.usefixtures("auth_route")
def test_pagination_respects_api_caps(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    total = 5000

    def handler(request: httpx.Request) -> httpx.Response:
        start, end = (int(x) for x in request.url.params["range"].split("-"))
        results = [{"id": f"OFF{i}"} for i in range(start, end + 1)]
        return httpx.Response(
            206,
            json={"resultats": results},
            headers={"Content-Range": f"offres {start}-{end}/{total}"},
        )

    route = respx_mock.get(SEARCH_URL).mock(side_effect=handler)
    with make_client(fake_time) as client:
        result = client.search(make_query())

    ranges = [call.request.url.params["range"] for call in route.calls]
    assert ranges == [
        "0-149",
        "150-299",
        "300-449",
        "450-599",
        "600-749",
        "750-899",
        "900-1049",
        "1000-1149",  # premier index ≤ 1000, second ≤ 1149
    ]
    assert len({offer.id for offer in result.offers}) == 1150
    assert result.truncated is True


@pytest.mark.usefixtures("auth_route")
def test_invalid_offers_are_skipped(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"resultats": [{"intitule": "sans id"}, {"id": "OK1"}, "pas un objet"]}
        )
    )
    with make_client(fake_time) as client:
        result = client.search(make_query())
    assert [offer.id for offer in result.offers] == ["OK1"]
    assert result.invalid == 1


@pytest.mark.usefixtures("auth_route")
def test_fetch_raw_limits_range(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=page("ft_search_page1.json", "offres 0-1/40")
    )
    with make_client(fake_time) as client:
        raw = client.fetch_raw(make_query(), limit=2)
    assert route.calls[0].request.url.params["range"] == "0-1"
    assert route.call_count == 1
    assert [item["id"] for item in raw] == ["200AAAA", "200BBBB"]


# --------------------------------------------------------------------------- erreurs & retries


@pytest.mark.usefixtures("auth_route")
def test_429_respects_retry_after(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(204)]
    )
    with make_client(fake_time) as client:
        client.search(make_query())
    assert route.call_count == 2
    assert 2.0 in fake_time.sleeps


@pytest.mark.usefixtures("auth_route")
def test_5xx_exhausts_retries(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    with make_client(fake_time) as client, pytest.raises(SearchError, match="HTTP 503"):
        client.search(make_query())
    assert route.call_count == 5
    assert [s for s in fake_time.sleeps if s >= 1] == [1, 2, 4, 8]


@pytest.mark.usefixtures("auth_route")
def test_transport_error_is_retried(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[httpx.ConnectTimeout("timeout"), httpx.Response(204)]
    )
    with make_client(fake_time) as client:
        client.search(make_query())
    assert route.call_count == 2


def test_401_renews_token_once(
    respx_mock: respx.MockRouter, auth_route: respx.Route, fake_time: FakeTime
) -> None:
    respx_mock.get(SEARCH_URL).mock(side_effect=[httpx.Response(401), httpx.Response(204)])
    with make_client(fake_time) as client:
        client.search(make_query())
    assert auth_route.call_count == 2


@pytest.mark.usefixtures("auth_route")
@pytest.mark.parametrize("status", [401, 403])
def test_persistent_401_or_403_is_auth_error(
    respx_mock: respx.MockRouter, fake_time: FakeTime, status: int
) -> None:
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(status))
    with make_client(fake_time) as client, pytest.raises(AuthError, match=str(status)):
        client.search(make_query())


@pytest.mark.usefixtures("auth_route")
def test_400_is_not_retried(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            400, json={"codeErreur": "PARAM", "message": "motsCles invalide"}
        )
    )
    with make_client(fake_time) as client, pytest.raises(SearchError, match="motsCles invalide"):
        client.search(make_query())
    assert route.call_count == 1


@pytest.mark.usefixtures("auth_route")
def test_unreadable_json(respx_mock: respx.MockRouter, fake_time: FakeTime) -> None:
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(200, text="<html>"))
    with make_client(fake_time) as client, pytest.raises(SearchError, match="illisible"):
        client.search(make_query())


@pytest.mark.usefixtures("auth_route")
def test_counts_http_requests_and_throttles(
    respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    respx_mock.get(SEARCH_URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(204), httpx.Response(204)]
    )
    with make_client(fake_time) as client:
        client.search(make_query())
        client.search(make_query())
        assert client.http_requests == 4  # 1 auth + 3 recherches
    assert fake_time.sleeps.count(0.25) >= 2  # espacement à 4 req/s


def test_invalid_page_size() -> None:
    with pytest.raises(ValueError, match="page_size"):
        FranceTravailClient("a", "b", page_size=151)


def test_injected_http_client_is_not_closed(fake_time: FakeTime) -> None:
    http = httpx.Client()
    with make_client(fake_time, http=http):
        pass
    assert http.is_closed is False
    http.close()
