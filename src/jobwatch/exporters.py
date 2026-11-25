"""Exports fichiers : CSV pour le tableau de suivi, digest Markdown."""

import csv
import re
from pathlib import Path

from jobwatch.digest import Digest, ScoredOffer, format_date, plural

CSV_COLUMNS = (
    "Priorité",
    "Entreprise",
    "Ville(s)",
    "Poste à viser",
    "Statut",
    "Pourquoi / signal trouvé",
    "Contact à chercher sur LinkedIn",
    "Type",
    "Date de contact",
    "Relance le",
    "Source",
    "Notes",
)
CSV_STATUS = "À contacter"
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_MD_SPECIALS = re.compile(r"([\\`*_{}\[\]<>#|])")


def csv_path(out_dir: Path, digest: Digest) -> Path:
    return out_dir / f"jobwatch-{digest.run_date:%Y-%m-%d}.csv"


def markdown_path(out_dir: Path, digest: Digest) -> Path:
    return out_dir / f"digest-{digest.run_date:%Y-%m-%d}.md"


def _cell(value: str) -> str:
    """Neutralise l'injection de formules à l'ouverture dans un tableur."""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def reasons_text(item: ScoredOffer) -> str:
    return " · ".join(str(reason) for reason in item.result.top_reasons(len(item.result.reasons)))


def linkedin_contact(item: ScoredOffer) -> str:
    company = f"{item.offer.company} " if item.offer.has_company else ""
    return f"« Talent Acquisition » {company}{item.zone}"


def csv_row(item: ScoredOffer) -> list[str]:
    offer = item.offer
    notes = " · ".join(
        part
        for part in (
            f"Score {item.score}/100",
            offer.experience,
            offer.salary,
            f"publiée le {format_date(offer.date_creation)}" if offer.date_creation else "",
        )
        if part
    )
    row = [
        str(item.priority),
        offer.company,
        item.city,
        offer.intitule,
        CSV_STATUS,
        reasons_text(item),
        linkedin_contact(item),
        offer.type_contrat or "CDI",
        "",
        "",
        offer.url,
        notes,
    ]
    return [_cell(value) for value in row]


def write_csv(digest: Digest, out_dir: Path) -> Path:
    """CSV UTF-8 avec BOM, séparateur `;` : s'ouvre tel quel dans un tableur en français."""
    path = csv_path(out_dir, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writerow(CSV_COLUMNS)
        writer.writerows(csv_row(item) for item in digest.ranked())
    return path


def md_escape(text: str) -> str:
    return _MD_SPECIALS.sub(r"\\\1", text)


def render_markdown(digest: Digest) -> str:
    lines = [f"# jobwatch · digest du {digest.run_date_label}", ""]
    analysed = plural(digest.analysed, "offre analysée")
    if not digest.items:
        lines += [
            f"_Rien de neuf_ (fenêtre {digest.window}, {analysed}, seuil {digest.min_score}).",
            "",
        ]
        return "\n".join(lines)

    lines += [
        f"**{plural(len(digest.items), 'nouvelle offre')}** · fenêtre {digest.window} · "
        f"seuil {digest.min_score} · {analysed}",
        "",
    ]
    for zone, items in digest.groups():
        lines += [f"## 📍 {md_escape(zone)} ({len(items)})", ""]
        for item in items:
            offer = item.offer
            lines += [
                f"### [{md_escape(offer.intitule or 'Offre')}](<{offer.url}>) · "
                f"{item.score}/100 (priorité {item.priority})",
                "",
                f"- **Entreprise** : {md_escape(offer.company)}",
                f"- **Ville** : {md_escape(item.city)}",
            ]
            if offer.salary:
                lines.append(f"- **Salaire** : {md_escape(offer.salary)}")
            if offer.experience:
                lines.append(f"- **Expérience** : {md_escape(offer.experience)}")
            if offer.date_creation:
                lines.append(f"- **Publiée le** : {format_date(offer.date_creation)}")
            lines += [f"- **Pourquoi** : {md_escape(reasons_text(item))}", ""]
    return "\n".join(lines)


def write_markdown(digest: Digest, out_dir: Path) -> Path:
    path = markdown_path(out_dir, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(digest), encoding="utf-8")
    return path
