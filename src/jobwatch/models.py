"""Modèles pydantic d'une offre de l'API Offres d'emploi v2.

Volontairement tolérants : champs optionnels, `extra="ignore"`, et les `null` renvoyés par l'API
retombent sur la valeur par défaut. Seul `id` est obligatoire.
"""

import re
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

FALLBACK_URL = "https://candidat.francetravail.fr/offres/recherche/detail/{id}"
UNKNOWN_COMPANY = "Entreprise non communiquée"
EXPERIENCE_LABELS = {
    "D": "Débutant accepté",
    "S": "Expérience souhaitée",
    "E": "Expérience exigée",
}
_DEPT_PREFIX_RE = re.compile(r"^\s*(?:\d{2,3}|2[AB])\s*-\s*")


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data


class LieuTravail(_ApiModel):
    libelle: str = ""
    code_postal: str = Field(default="", alias="codePostal")


class Entreprise(_ApiModel):
    nom: str = ""


class Salaire(_ApiModel):
    libelle: str = ""
    commentaire: str = ""


class OrigineOffre(_ApiModel):
    url_origine: str = Field(default="", alias="urlOrigine")


class Offer(_ApiModel):
    id: str = Field(min_length=1)
    intitule: str = ""
    description: str = ""
    date_creation: datetime | None = Field(default=None, alias="dateCreation")
    lieu_travail: LieuTravail = Field(default_factory=LieuTravail, alias="lieuTravail")
    entreprise: Entreprise = Field(default_factory=Entreprise)
    type_contrat: str = Field(default="", alias="typeContrat")
    experience_exige: str = Field(default="", alias="experienceExige")
    experience_libelle: str = Field(default="", alias="experienceLibelle")
    salaire: Salaire = Field(default_factory=Salaire)
    origine_offre: OrigineOffre = Field(default_factory=OrigineOffre, alias="origineOffre")
    alternance: bool = False

    _raw: dict[str, Any] = PrivateAttr(default_factory=dict)

    @field_validator("date_creation", mode="wrap")
    @classmethod
    def _lenient_date(cls, value: Any, handler: ValidatorFunctionWrapHandler) -> datetime | None:
        """Une date illisible ne doit pas faire perdre l'offre ; une date naïve est en UTC."""
        try:
            parsed: datetime | None = handler(value)
        except ValidationError:
            return None
        if parsed is not None and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Self:
        """Valide une offre brute en conservant le JSON d'origine (stocké tel quel en base)."""
        offer = cls.model_validate(data)
        offer._raw = data
        return offer

    @property
    def payload(self) -> dict[str, Any]:
        return self._raw or self.model_dump(mode="json", by_alias=True)

    @property
    def url(self) -> str:
        candidate = self.origine_offre.url_origine.strip()
        if candidate.startswith(("https://", "http://")) and " " not in candidate:
            return candidate
        return FALLBACK_URL.format(id=self.id)

    @property
    def company(self) -> str:
        return self.entreprise.nom.strip() or UNKNOWN_COMPANY

    @property
    def has_company(self) -> bool:
        return bool(self.entreprise.nom.strip())

    @property
    def city(self) -> str:
        """« 75 - Paris 9e Arrondissement » → « Paris 9e Arrondissement »."""
        return _DEPT_PREFIX_RE.sub("", self.lieu_travail.libelle).strip()

    @property
    def salary(self) -> str:
        return self.salaire.libelle.strip() or self.salaire.commentaire.strip()

    @property
    def experience(self) -> str:
        return self.experience_libelle.strip() or EXPERIENCE_LABELS.get(self.experience_exige, "")
