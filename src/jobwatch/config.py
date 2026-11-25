"""Configuration fonctionnelle (`config.yaml`) : recherches, scoring et notification.

Le fichier est validé strictement (`extra="forbid"`) pour qu'une faute de frappe dans une clé
soit signalée au lieu d'être silencieusement ignorée.
"""

import re
from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from jobwatch.errors import ConfigError

_DEPARTMENT_RE = re.compile(r"^(?:\d{2}|2[AB]|97\d)$")


def _clean_terms(values: list[str]) -> list[str]:
    """Normalise les espaces et retire les doublons (insensible à la casse), ordre conservé."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        term = " ".join(raw.split())
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            cleaned.append(term)
    return cleaned


Terms = Annotated[list[str], AfterValidator(_clean_terms)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SearchConfig(_Strict):
    contract_type: str = "CDI"
    departments: dict[str, str] = Field(min_length=1)
    keywords: Terms = Field(min_length=1)

    @field_validator("departments")
    @classmethod
    def _check_departments(cls, value: dict[str, str]) -> dict[str, str]:
        for code, label in value.items():
            if not _DEPARTMENT_RE.match(code):
                raise ValueError(f"code département invalide : {code!r}")
            if not label.strip():
                raise ValueError(f"libellé vide pour le département {code}")
        return value

    @field_validator("keywords")
    @classmethod
    def _check_keywords(cls, value: list[str]) -> list[str]:
        too_short = [kw for kw in value if len(kw) < 2]
        if too_short:
            raise ValueError(f"mots-clés de moins de 2 caractères refusés par l'API : {too_short}")
        return value

    def queries(self) -> list[tuple[str, str]]:
        """Couples (mot-clé, département) : une requête API par couple."""
        return [(kw, dept) for kw in self.keywords for dept in self.departments]

    def zone(self, department: str) -> str:
        return self.departments.get(department, department)


class ExperienceWeights(_Strict):
    debutant: int = 20
    souhaitee: int = 0
    exigee: int = -10
    exigee_longue: int = -25
    annees_longue: float = Field(default=3, gt=0)


class JuniorRule(_Strict):
    weight: int = 15
    keywords: Terms


class StackRule(_Strict):
    per_tech: int = Field(default=4, ge=0)
    max_bonus: int = Field(default=24, ge=0, alias="max")
    techs: Terms
    aliases: dict[str, Terms] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _aliases_known(self) -> Self:
        unknown = set(self.aliases) - set(self.techs)
        if unknown:
            raise ValueError(
                f"alias déclarés pour des technos absentes de `techs` : {sorted(unknown)}"
            )
        return self


class SeniorityRule(_Strict):
    title_weight: int = -25
    keywords: Terms
    description_weight: int = -8
    description_keywords: Terms = Field(default_factory=list)


class ExclusionRule(_Strict):
    keywords: Terms
    check_description: bool = False


class ScoringConfig(_Strict):
    base: int = Field(default=40, ge=0, le=100)
    experience: ExperienceWeights = Field(default_factory=ExperienceWeights)
    junior: JuniorRule
    stack: StackRule
    seniority: SeniorityRule
    exclusions: ExclusionRule


class NotifyConfig(_Strict):
    min_score: int = Field(default=40, ge=0, le=100)


class AppConfig(_Strict):
    search: SearchConfig
    scoring: ScoringConfig
    notify: NotifyConfig = Field(default_factory=NotifyConfig)


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "(racine)"
        lines.append(f"  - {location} : {err['msg']}")
    return "\n".join(lines)


def load_config(path: Path) -> AppConfig:
    """Charge et valide `config.yaml`, ou lève `ConfigError` avec un message lisible."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Fichier de configuration introuvable : {path}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML invalide dans {path} : {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} doit contenir un objet YAML (clés search, scoring, notify).")
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Configuration invalide ({path}) :\n{_format_errors(exc)}") from exc
