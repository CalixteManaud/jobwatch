import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from jobwatch.digest import Digest, ScoredOffer, group_by_zone, plural
from jobwatch.exporters import (
    CSV_COLUMNS,
    linkedin_contact,
    md_escape,
    render_markdown,
    write_csv,
    write_markdown,
)
from jobwatch.scoring import Reason

from .conftest import make_scored

NOW = datetime(2026, 11, 25, 5, 30, tzinfo=UTC)
EXPECTED_HEADER = (
    "Priorité;Entreprise;Ville(s);Poste à viser;Statut;Pourquoi / signal trouvé;"
    "Contact à chercher sur LinkedIn;Type;Date de contact;Relance le;Source;Notes"
)


def digest(items: list[ScoredOffer]) -> Digest:
    return Digest(generated_at=NOW, window="26h", min_score=40, analysed=12, items=items)


def junior() -> ScoredOffer:
    return make_scored(
        "200AAAA",
        score=95,
        intitule="Ingénieur DevOps Junior H/F",
        entreprise={"nom": "NUAGE CONSEIL"},
        lieuTravail={"libelle": "75 - Paris 15e Arrondissement"},
        salaire={"libelle": "Annuel de 40k à 45k"},
        experienceLibelle="Débutant accepté",
        dateCreation="2026-11-24T09:12:44Z",
        reasons=(Reason(20, "débutant accepté"), Reason(8, "stack : aws, docker")),
    )


def read_csv(path: Path) -> tuple[bytes, list[list[str]]]:
    raw = path.read_bytes()
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), delimiter=";"))
    return raw, rows


# --------------------------------------------------------------------------- CSV


def test_csv_format_bom_separator_and_exact_columns(tmp_path: Path) -> None:
    path = write_csv(digest([junior()]), tmp_path / "out")
    assert path == tmp_path / "out" / "jobwatch-2026-11-25.csv"
    raw, rows = read_csv(path)
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines()[0] == EXPECTED_HEADER
    assert tuple(rows[0]) == CSV_COLUMNS
    assert len(CSV_COLUMNS) == 12
    assert dict(zip(rows[0], rows[1], strict=True)) == {
        "Priorité": "1",
        "Entreprise": "NUAGE CONSEIL",
        "Ville(s)": "Paris 15e Arrondissement",
        "Poste à viser": "Ingénieur DevOps Junior H/F",
        "Statut": "À contacter",
        "Pourquoi / signal trouvé": "débutant accepté (+20) · stack : aws, docker (+8)",
        "Contact à chercher sur LinkedIn": "« Talent Acquisition » NUAGE CONSEIL Paris",
        "Type": "CDI",
        "Date de contact": "",
        "Relance le": "",
        "Source": "https://candidat.francetravail.fr/offres/recherche/detail/200AAAA",
        "Notes": "Score 95/100 · Débutant accepté · Annuel de 40k à 45k · publiée le 24/11/2026",
    }


def test_csv_priorities_and_order(tmp_path: Path) -> None:
    items = [make_scored("C", 49), make_scored("A", 70), make_scored("B", 50, zone="Lyon")]
    _, rows = read_csv(write_csv(digest(items), tmp_path))
    assert [(row[0], row[3]) for row in rows[1:]] == [
        ("1", "Poste A"),
        ("2", "Poste B"),
        ("3", "Poste C"),
    ]


def test_csv_quotes_separator_and_neutralises_formulas(tmp_path: Path) -> None:
    item = make_scored(intitule='=HYPERLINK("x";"y")', entreprise={"nom": "A; B & Co"})
    raw, rows = read_csv(write_csv(digest([item]), tmp_path))
    assert rows[1][1] == "A; B & Co"
    assert rows[1][3] == '\'=HYPERLINK("x";"y")'
    assert '"A; B & Co"' in raw.decode("utf-8-sig")


def test_csv_empty_digest_has_header_only(tmp_path: Path) -> None:
    _, rows = read_csv(write_csv(digest([]), tmp_path))
    assert rows == [list(CSV_COLUMNS)]


def test_linkedin_contact_without_company() -> None:
    assert linkedin_contact(make_scored(zone="Lille")) == "« Talent Acquisition » Lille"


# --------------------------------------------------------------------------- Markdown


def test_markdown_digest(tmp_path: Path) -> None:
    items = [junior(), make_scored("200BBBB", 52, "Lyon", intitule="SRE [Cloud] *H/F*")]
    path = write_markdown(digest(items), tmp_path)
    assert path.name == "digest-2026-11-25.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# jobwatch · digest du 25/11/2026\n")
    assert "**2 nouvelles offres** · fenêtre 26h · seuil 40 · 12 offres analysées" in text
    assert text.index("## 📍 Paris (1)") < text.index("## 📍 Lyon (1)")
    assert (
        "### [Ingénieur DevOps Junior H/F]"
        "(<https://candidat.francetravail.fr/offres/recherche/detail/200AAAA>)"
        " · 95/100 (priorité 1)"
    ) in text
    assert "- **Salaire** : Annuel de 40k à 45k" in text
    assert "- **Publiée le** : 24/11/2026" in text
    assert r"### [SRE \[Cloud\] \*H/F\*]" in text


def test_markdown_nothing_new() -> None:
    text = render_markdown(digest([]))
    assert "_Rien de neuf_ (fenêtre 26h, 12 offres analysées, seuil 40)." in text


def test_md_escape() -> None:
    assert md_escape("a_b*c[d]#e|f") == r"a\_b\*c\[d\]\#e\|f"


# --------------------------------------------------------------------------- digest


def test_group_by_zone_sorts_by_score_then_recency() -> None:
    items = [
        make_scored("old", 60, "Paris", dateCreation="2026-11-20T00:00:00Z"),
        make_scored("new", 60, "Paris", dateCreation="2026-11-24T00:00:00Z"),
        make_scored("top", 80, "Lille"),
    ]
    groups = group_by_zone(items)
    assert [(zone, [i.offer.id for i in group]) for zone, group in groups] == [
        ("Lille", ["top"]),
        ("Paris", ["new", "old"]),
    ]


def test_plural() -> None:
    assert plural(0, "offre analysée") == "0 offre analysée"
    assert plural(1, "nouvelle offre") == "1 nouvelle offre"
    assert plural(3, "nouvelle offre") == "3 nouvelles offres"
