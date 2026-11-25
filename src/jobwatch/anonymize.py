"""Anonymisation d'offres brutes avant de les enregistrer comme fixtures de test.

On garde la structure exacte de la réponse (c'est tout l'intérêt de `jobwatch sample`) mais on
remplace tout ce qui identifie une entreprise ou une personne : nom, contact, emails, téléphones.
"""

import copy
import re
from typing import Any

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?:(?:\+|00)33[\s.]?|0)[1-9](?:[\s.-]?\d{2}){4}")

# section → clé → valeur de remplacement (None = clé supprimée)
_REPLACEMENTS: dict[str, dict[str, str | None]] = {
    "entreprise": {
        "nom": "ENTREPRISE {n}",
        "description": "Description de l'entreprise anonymisée.",
        "logo": None,
        "url": None,
        "siret": None,
    },
    "contact": {
        "nom": "Contact anonymisé",
        "coordonnees1": "1 rue de l'Exemple",
        "coordonnees2": None,
        "coordonnees3": "75000 PARIS",
        "telephone": "01 00 00 00 00",
        "courriel": "contact@example.com",
        "commentaire": None,
        "urlRecruteur": "https://example.com",
        "urlPostulation": "https://example.com/postuler",
    },
    "agence": {"telephone": "01 00 00 00 00", "courriel": "agence@example.com"},
}


def _replace_email(match: re.Match[str]) -> str:
    """Remplace toute adresse réelle ; laisse intactes les adresses d'exemple déjà posées."""
    address = match.group(0)
    return address if address.endswith("@example.com") else "contact@example.com"


def _scrub(value: Any, names: list[re.Pattern[str]], index: int) -> Any:
    if isinstance(value, str):
        value = _EMAIL_RE.sub(_replace_email, value)
        value = _PHONE_RE.sub("01 00 00 00 00", value)
        for pattern in names:
            value = pattern.sub(f"ENTREPRISE {index}", value)
        return value
    if isinstance(value, dict):
        return {key: _scrub(item, names, index) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, names, index) for item in value]
    return value


def anonymize_offer(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Copie anonymisée d'une offre brute (l'original n'est pas modifié)."""
    data = copy.deepcopy(raw)
    company = raw.get("entreprise")
    original_name = company.get("nom") if isinstance(company, dict) else None
    names = (
        [re.compile(re.escape(original_name.strip()), re.IGNORECASE)]
        if isinstance(original_name, str) and len(original_name.strip()) >= 3
        else []
    )
    for section, replacements in _REPLACEMENTS.items():
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for key, replacement in replacements.items():
            if key not in block:
                continue
            if replacement is None:
                del block[key]
            else:
                block[key] = replacement.format(n=index)
    scrubbed: dict[str, Any] = _scrub(data, names, index)
    return scrubbed


def anonymize_offers(raw_offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [anonymize_offer(raw, index) for index, raw in enumerate(raw_offers, start=1)]


EXPECTED_FIELDS = (
    "id",
    "intitule",
    "description",
    "dateCreation",
    "lieuTravail.libelle",
    "entreprise.nom",
    "typeContrat",
    "experienceExige",
    "experienceLibelle",
    "salaire.libelle",
    "origineOffre.urlOrigine",
    "alternance",
)


def field_presence(raw_offers: list[dict[str, Any]]) -> dict[str, int]:
    """Pour chaque champ attendu par les modèles : nombre d'offres où il est renseigné."""
    counts = dict.fromkeys(EXPECTED_FIELDS, 0)
    for raw in raw_offers:
        for dotted in EXPECTED_FIELDS:
            node: Any = raw
            for part in dotted.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if node is not None:
                counts[dotted] += 1
    return counts
