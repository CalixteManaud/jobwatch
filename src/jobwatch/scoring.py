"""Scoring 0-100 d'une offre, avec la raison de chaque point (poids dans `config.yaml`).

score = base + expérience + signal junior + stack (plafonnée) + malus séniorité, borné à [0, 100].
Une offre exclue (alternance, stage, freelance...) vaut 0 et n'est jamais envoyée.
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from jobwatch.config import ScoringConfig
from jobwatch.models import Offer

SCORE_MIN = 0
SCORE_MAX = 100
PRIORITY_1_MIN = 70
PRIORITY_2_MIN = 50

_SUFFIX = r"(?:e|s|es)?"  # pluriel / féminin : confirmé → confirmée(s)
_YEARS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*an(?:s|\(s\))?(?!\w)")
_MONTHS_RE = re.compile(r"(\d+)\s*mois(?!\w)")


def normalize(text: str) -> str:
    """Minuscules, sans accents, apostrophes unifiées, espaces compactés."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_marks.replace("’", "'").casefold().split())


@lru_cache(maxsize=1024)
def term_pattern(term: str) -> re.Pattern[str]:
    """Motif « mot entier » tolérant : « jeune diplômé » matche « jeunes diplômées »,
    « expert » ne matche pas « expertise », « 5 ans » ne matche pas « 15 ans »."""
    words = normalize(term).replace("-", " ").split()
    parts = [re.escape(word) + ("" if word.isdigit() else _SUFFIX) for word in words]
    return re.compile(r"(?<!\w)" + r"[\s\-]*".join(parts) + r"(?!\w)")


def find_terms(terms: list[str], normalized_text: str) -> list[str]:
    """Termes (tels qu'écrits dans la config) présents dans un texte déjà normalisé."""
    return [term for term in terms if term_pattern(term).search(normalized_text)]


def required_years(label: str) -> float | None:
    """« 5 An(s) » → 5 ; « Expérience exigée de 24 Mois » → 2 ; rien d'exploitable → None."""
    text = normalize(label)
    values = [float(value.replace(",", ".")) for value in _YEARS_RE.findall(text)]
    values += [int(value) / 12 for value in _MONTHS_RE.findall(text)]
    return max(values) if values else None


@dataclass(frozen=True, slots=True)
class Reason:
    points: int
    label: str

    def __str__(self) -> str:
        return f"{self.label} ({self.points:+d})" if self.points else self.label


@dataclass(frozen=True, slots=True)
class ScoreResult:
    score: int
    reasons: tuple[Reason, ...]
    excluded_by: str | None = None

    @property
    def excluded(self) -> bool:
        return self.excluded_by is not None

    @property
    def priority(self) -> int:
        """1 si score ≥ 70, 2 si ≥ 50, sinon 3."""
        if self.score >= PRIORITY_1_MIN:
            return 1
        if self.score >= PRIORITY_2_MIN:
            return 2
        return 3

    def top_reasons(self, count: int = 3) -> list[Reason]:
        """Raisons les plus déterminantes (en valeur absolue)."""
        return sorted(self.reasons, key=lambda reason: abs(reason.points), reverse=True)[:count]


class Scorer:
    def __init__(self, config: ScoringConfig, *, contract_type: str = "CDI") -> None:
        self._config = config
        self._contract_type = contract_type
        stack = config.stack
        self._tech_terms = {tech: [tech, *stack.aliases.get(tech, [])] for tech in stack.techs}

    def score(self, offer: Offer) -> ScoreResult:
        title = normalize(offer.intitule)
        description = normalize(offer.description)
        anywhere = f"{title}\n{description}"

        exclusion = self._exclusion(offer, title, description)
        if exclusion is not None:
            return ScoreResult(SCORE_MIN, (Reason(0, f"exclue : {exclusion}"),), exclusion)

        reasons = [
            *self._experience(offer),
            *self._junior(anywhere),
            *self._stack(anywhere),
            *self._seniority(title, description),
        ]
        reasons = [reason for reason in reasons if reason.points != 0]
        raw = self._config.base + sum(reason.points for reason in reasons)
        return ScoreResult(max(SCORE_MIN, min(SCORE_MAX, raw)), tuple(reasons))

    # -- règles
    def _exclusion(self, offer: Offer, title: str, description: str) -> str | None:
        if offer.alternance:
            return "alternance"
        if offer.type_contrat and offer.type_contrat != self._contract_type:
            return f"contrat {offer.type_contrat}"
        rule = self._config.exclusions
        if found := find_terms(rule.keywords, title):
            return f"« {found[0]} » dans le titre"
        if rule.check_description and (found := find_terms(rule.keywords, description)):
            return f"« {found[0]} » dans la description"
        return None

    def _experience(self, offer: Offer) -> list[Reason]:
        weights = self._config.experience
        match offer.experience_exige:
            case "D":
                return [Reason(weights.debutant, "débutant accepté")]
            case "S":
                return [Reason(weights.souhaitee, "expérience souhaitée")]
            case "E":
                years = required_years(offer.experience_libelle)
                if years is not None and years >= weights.annees_longue:
                    return [Reason(weights.exigee_longue, f"expérience exigée ({years:g} ans)")]
                detail = f" ({years:g} ans)" if years is not None else ""
                return [Reason(weights.exigee, f"expérience exigée{detail}")]
            case _:
                return []

    def _junior(self, text: str) -> list[Reason]:
        rule = self._config.junior
        found = find_terms(rule.keywords, text)
        return [Reason(rule.weight, f"signal junior : {', '.join(found)}")] if found else []

    def _stack(self, text: str) -> list[Reason]:
        rule = self._config.stack
        techs = [tech for tech, terms in self._tech_terms.items() if find_terms(terms, text)]
        if not techs:
            return []
        bonus = min(len(techs) * rule.per_tech, rule.max_bonus)
        return [Reason(bonus, f"stack : {', '.join(techs)}")]

    def _seniority(self, title: str, description: str) -> list[Reason]:
        rule = self._config.seniority
        reasons = []
        if in_title := find_terms(rule.keywords, title):
            reasons.append(Reason(rule.title_weight, f"séniorité (titre) : {', '.join(in_title)}"))
        if in_description := find_terms(rule.description_keywords, description):
            reasons.append(
                Reason(
                    rule.description_weight,
                    f"séniorité (description) : {', '.join(in_description)}",
                )
            )
        return reasons
