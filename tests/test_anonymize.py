from jobwatch.anonymize import EXPECTED_FIELDS, anonymize_offer, field_presence

from .conftest import load_json

RAW = {
    "id": "201XYZ",
    "intitule": "DevOps chez Acme Cloud",
    "description": (
        "Acme Cloud recrute ! Écrire à jean.dupont@acme-cloud.fr "
        "ou appeler le 06 12 34 56 78 / +33 1 23 45 67 89."
    ),
    "entreprise": {"nom": "ACME CLOUD", "logo": "https://x/logo.png", "url": "https://acme"},
    "contact": {
        "nom": "M. Jean Dupont",
        "courriel": "jean.dupont@acme-cloud.fr",
        "telephone": "0612345678",
        "coordonnees1": "12 rue Secrète",
        "commentaire": "Appeler le matin",
    },
    "agence": {"courriel": "agence75@francetravail.fr"},
    "lieuTravail": {"libelle": "75 - Paris 2e Arrondissement"},
}


def test_anonymize_replaces_identifying_data() -> None:
    data = anonymize_offer(RAW, 7)
    assert data["id"] == "201XYZ"
    assert data["entreprise"] == {
        "nom": "ENTREPRISE 7",
    }
    assert data["contact"] == {
        "nom": "Contact anonymisé",
        "courriel": "contact@example.com",
        "telephone": "01 00 00 00 00",
        "coordonnees1": "1 rue de l'Exemple",
    }
    assert data["agence"] == {"courriel": "agence@example.com"}
    assert data["intitule"] == "DevOps chez ENTREPRISE 7"
    assert data["description"] == (
        "ENTREPRISE 7 recrute ! Écrire à contact@example.com "
        "ou appeler le 01 00 00 00 00 / 01 00 00 00 00."
    )
    assert data["lieuTravail"] == RAW["lieuTravail"]


def test_anonymize_does_not_modify_original() -> None:
    anonymize_offer(RAW, 1)
    assert RAW["entreprise"]["nom"] == "ACME CLOUD"  # type: ignore[index]


def test_anonymize_without_company() -> None:
    data = anonymize_offer({"id": "1", "entreprise": None, "description": "rien"}, 1)
    assert data == {"id": "1", "entreprise": None, "description": "rien"}


def test_field_presence() -> None:
    raw = load_json("ft_search_page2.json")["resultats"]
    presence = field_presence(raw)
    assert set(presence) == set(EXPECTED_FIELDS)
    assert presence["id"] == 2
    assert presence["entreprise.nom"] == 1  # null pour la 2e offre
    assert presence["origineOffre.urlOrigine"] == 1
    assert presence["salaire.libelle"] == 0
