from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jobwatch.models import FALLBACK_URL, UNKNOWN_COMPANY, Offer

from .conftest import load_json


def _offers(name: str) -> list[Offer]:
    return [Offer.from_api(raw) for raw in load_json(name)["resultats"]]


def test_full_offer_from_fixture() -> None:
    offer = _offers("ft_search_page1.json")[0]
    assert offer.id == "200AAAA"
    assert offer.intitule == "Ingénieur DevOps Junior H/F"
    assert offer.date_creation == datetime(2026, 11, 24, 9, 12, 44, tzinfo=UTC)
    assert offer.lieu_travail.code_postal == "75015"
    assert offer.city == "Paris 15e Arrondissement"
    assert offer.company == "NUAGE CONSEIL"
    assert offer.type_contrat == "CDI"
    assert offer.experience_exige == "D"
    assert offer.experience == "Débutant accepté"
    assert offer.salary.startswith("Annuel de 38000")
    assert offer.url == "https://candidat.francetravail.fr/offres/recherche/detail/200AAAA"
    assert offer.alternance is False


def test_nulls_and_missing_fields_fall_back_to_defaults() -> None:
    offer = _offers("ft_search_page2.json")[1]
    assert offer.id == "200DDDD"
    assert offer.company == UNKNOWN_COMPANY
    assert offer.has_company is False
    assert offer.salary == ""
    assert offer.url == FALLBACK_URL.format(id="200DDDD")


def test_minimal_offer() -> None:
    offer = Offer.from_api({"id": "X1", "champInconnu": {"a": 1}})
    assert offer.intitule == ""
    assert offer.city == ""
    assert offer.experience == ""
    assert offer.payload == {"id": "X1", "champInconnu": {"a": 1}}


def test_id_is_required() -> None:
    with pytest.raises(ValidationError):
        Offer.from_api({"intitule": "sans identifiant"})
    with pytest.raises(ValidationError):
        Offer.from_api({"id": ""})


def test_experience_label_falls_back_to_code() -> None:
    assert Offer.from_api({"id": "1", "experienceExige": "E"}).experience == "Expérience exigée"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-24T09:12:44Z", datetime(2026, 11, 24, 9, 12, 44, tzinfo=UTC)),
        ("2026-11-24T09:12:44", datetime(2026, 11, 24, 9, 12, 44, tzinfo=UTC)),
        ("pas une date", None),
    ],
)
def test_lenient_creation_date(value: str, expected: datetime | None) -> None:
    assert Offer.from_api({"id": "1", "dateCreation": value}).date_creation == expected


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "https://exemple.fr/avec espace", "", "ftp://x"]
)
def test_unsafe_or_missing_url_uses_fallback(url: str) -> None:
    offer = Offer.from_api({"id": "ZZ9", "origineOffre": {"urlOrigine": url}})
    assert offer.url == FALLBACK_URL.format(id="ZZ9")


@pytest.mark.parametrize(
    ("libelle", "city"),
    [
        ("59 - Lille", "Lille"),
        ("2A - Ajaccio", "Ajaccio"),
        ("974 - Saint-Denis", "Saint-Denis"),
        ("Lyon 3e", "Lyon 3e"),
    ],
)
def test_city_strips_department_prefix(libelle: str, city: str) -> None:
    assert Offer.from_api({"id": "1", "lieuTravail": {"libelle": libelle}}).city == city


def test_payload_without_raw_is_dumped() -> None:
    offer = Offer.model_validate({"id": "1", "intitule": "x"})
    assert offer.payload["id"] == "1"
    assert offer.payload["intitule"] == "x"
