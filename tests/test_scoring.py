from typing import Any

import pytest

from jobwatch.config import AppConfig, ScoringConfig
from jobwatch.models import Offer
from jobwatch.scoring import (
    Reason,
    Scorer,
    ScoreResult,
    find_terms,
    normalize,
    required_years,
    term_pattern,
)

from .conftest import load_json


@pytest.fixture
def scorer(app_config: AppConfig) -> Scorer:
    return Scorer(app_config.scoring)


def fixture_offer(page: int, index: int) -> Offer:
    return Offer.from_api(load_json(f"ft_search_page{page}.json")["resultats"][index])


def make_offer(**fields: Any) -> Offer:
    return Offer.from_api({"id": "T1", "typeContrat": "CDI", **fields})


def labels(result: ScoreResult) -> list[str]:
    return [str(reason) for reason in result.reasons]


# --------------------------------------------------------------------------- cas de référence


def test_junior_offer_scores_high(scorer: Scorer) -> None:
    result = scorer.score(fixture_offer(1, 0))
    # 40 + 20 (D) + 15 (junior) + 5 technos x 4
    assert result.score == 95
    assert result.priority == 1
    assert not result.excluded
    assert labels(result) == [
        "débutant accepté (+20)",
        "signal junior : junior, jeune diplômé (+15)",
        "stack : terraform, kubernetes, gitlab, aws, docker (+20)",
    ]


def test_senior_offer_scores_low(scorer: Scorer) -> None:
    result = scorer.score(fixture_offer(1, 1))
    # 40 - 25 (E, 5 ans) - 25 (titre) - 8 (description) + 12 (3 technos) = -6 → borné à 0
    assert result.score == 0
    assert result.priority == 3
    assert not result.excluded
    assert labels(result) == [
        "expérience exigée (5 ans) (-25)",
        "stack : terraform, kubernetes, aws (+12)",
        "séniorité (titre) : senior, architecte (-25)",
        "séniorité (description) : senior, 5 ans (-8)",
    ]


def test_alternance_is_excluded(scorer: Scorer) -> None:
    result = scorer.score(fixture_offer(2, 0))
    assert result.excluded
    assert result.excluded_by == "alternance"
    assert result.score == 0
    assert labels(result) == ["exclue : alternance"]


def test_neutral_offer(scorer: Scorer) -> None:
    result = scorer.score(fixture_offer(2, 1))
    assert result.score == 48  # 40 + linux + ansible ; « souhaitée » vaut 0 et n'est pas listée
    assert result.priority == 3
    assert labels(result) == ["stack : ansible, linux (+8)"]


# --------------------------------------------------------------------------- règles


@pytest.mark.parametrize(
    "title",
    ["Stage DevOps", "Ingénieur Cloud - Freelance", "DevOps en portage salarial", "Intérim"],
)
def test_exclusion_keywords_in_title(scorer: Scorer, title: str) -> None:
    assert scorer.score(make_offer(intitule=title)).excluded


def test_exclusion_in_description_is_opt_in(app_config: AppConfig) -> None:
    offer = make_offer(intitule="Ingénieur DevOps", description="Pas de freelance, CDI uniquement.")
    assert not Scorer(app_config.scoring).score(offer).excluded

    rule = app_config.scoring.exclusions.model_copy(update={"check_description": True})
    strict = Scorer(app_config.scoring.model_copy(update={"exclusions": rule}))
    assert strict.score(offer).excluded_by == "« freelance » dans la description"


def test_other_contract_type_is_excluded(scorer: Scorer) -> None:
    assert scorer.score(make_offer(typeContrat="MIS")).excluded_by == "contrat MIS"


@pytest.mark.parametrize(
    ("libelle", "points", "label"),
    [
        ("2 An(s)", -10, "expérience exigée (2 ans) (-10)"),
        ("Expérience exigée", -10, "expérience exigée (-10)"),
        ("3 An(s)", -25, "expérience exigée (3 ans) (-25)"),
        ("Expérience exigée de 36 Mois", -25, "expérience exigée (3 ans) (-25)"),
    ],
)
def test_required_experience(scorer: Scorer, libelle: str, points: int, label: str) -> None:
    result = scorer.score(make_offer(experienceExige="E", experienceLibelle=libelle))
    assert result.score == 40 + points
    assert labels(result) == [label]


def test_stack_bonus_is_capped(scorer: Scorer) -> None:
    description = "terraform kubernetes helm gitlab argocd aws docker ansible prometheus grafana"
    result = scorer.score(make_offer(description=description))
    assert result.score == 40 + 24


def test_aliases_count_once(scorer: Scorer) -> None:
    result = scorer.score(make_offer(description="Kubernetes (k8s) et Argo CD"))
    assert labels(result) == ["stack : kubernetes, argocd (+8)"]


def test_score_is_clamped_to_100(app_config: AppConfig) -> None:
    generous = Scorer(app_config.scoring.model_copy(update={"base": 95}))
    result = generous.score(make_offer(experienceExige="D", intitule="DevOps junior"))
    assert result.score == 100


def test_description_ignores_noisy_seniority_words(scorer: Scorer) -> None:
    offer = make_offer(description="Vous serez rattaché au lead tech et à votre manager.")
    assert scorer.score(offer).score == 40


def test_scoring_config_without_optional_rules(app_config: AppConfig) -> None:
    config = ScoringConfig.model_validate(
        {
            "junior": {"keywords": []},
            "stack": {"techs": []},
            "seniority": {"keywords": []},
            "exclusions": {"keywords": []},
        }
    )
    assert Scorer(config).score(make_offer(intitule="Senior junior docker")).score == 40


# --------------------------------------------------------------------------- utilitaires


def test_normalize() -> None:
    assert normalize("  Ingénieur   d’Exploitation  ÉTÉ ") == "ingenieur d'exploitation ete"


@pytest.mark.parametrize(
    ("term", "text", "expected"),
    [
        ("confirmé", "profil confirmée", True),
        ("confirmé", "profils confirmés", True),
        ("expert", "une expertise reconnue", False),
        ("expert", "experte linux", True),
        ("5 ans", "15 ans d'expérience", False),
        ("5 ans", "+5 ans d'expérience", True),
        ("5 ans", "5ans minimum", True),
        ("jeune diplômé", "jeunes diplômées bienvenues", True),
        ("première expérience", "une premiere experience reussie", True),
        ("k8s", "cluster K8S", True),
        ("argo cd", "argo-cd", True),
        ("lead", "leader du marché", False),
        ("senior", "séniorité", False),
        ("stage", "multi-stage", True),
    ],
)
def test_term_matching(term: str, text: str, expected: bool) -> None:
    assert bool(term_pattern(term).search(normalize(text))) is expected


def test_find_terms_keeps_config_spelling_and_order() -> None:
    assert find_terms(["Débutant", "junior"], normalize("JUNIOR ou débutante")) == [
        "Débutant",
        "junior",
    ]


@pytest.mark.parametrize(
    ("label", "years"),
    [
        ("5 An(s)", 5),
        ("Expérience exigée de 24 Mois", 2),
        ("3 ans - en ingénierie système", 3),
        ("1,5 an", 1.5),
        ("Débutant accepté", None),
        ("", None),
    ],
)
def test_required_years(label: str, years: float | None) -> None:
    assert required_years(label) == years


@pytest.mark.parametrize(("score", "priority"), [(100, 1), (70, 1), (69, 2), (50, 2), (49, 3)])
def test_priority_thresholds(score: int, priority: int) -> None:
    assert ScoreResult(score, ()).priority == priority


def test_reason_formatting_and_top_reasons() -> None:
    result = ScoreResult(
        60, (Reason(4, "stack : aws"), Reason(-25, "séniorité"), Reason(20, "débutant accepté"))
    )
    assert [str(r) for r in result.top_reasons(2)] == ["séniorité (-25)", "débutant accepté (+20)"]
    assert str(Reason(0, "exclue : stage")) == "exclue : stage"
