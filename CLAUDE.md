# jobwatch

Bot Python 3.12 qui surveille chaque matin les offres CDI DevOps / Cloud / Run de l'API France
Travail « Offres d'emploi v2 », les score (0–100, règles dans `config.yaml`) et livre un digest
Discord, un CSV (`;`, BOM) et un digest Markdown. État SQLite : une offre notifiée ne repart jamais.
Projet vitrine : code, CI/CD, Docker, Helm et doc doivent rester propres.

## Commandes

```bash
make install   # uv sync --frozen + pre-commit install
make lint      # ruff check, ruff format --check, mypy --strict (src/)
make test      # pytest + couverture (échec < 80 %)
make helm-lint # helm lint --strict + helm template
make docker-build / docker-run / kind-deploy / kind-job
uv run jobwatch run --dry-run --since 7d   # appel réel : demander avant
```

## Repères

- Code dans `src/jobwatch/` : `francetravail.py` (client API), `collect.py`, `scoring.py`,
  `store.py`, `pipeline.py`, `cli.py`, `discord.py`, `exporters.py`, `metrics.py`.
- Secrets uniquement par l'environnement (`.env` local, GitHub Secrets, Secret k8s) : jamais en dur.
- Tests sans réseau (respx + pytest-socket `--disable-socket`). Fixtures dans `tests/fixtures/`.
- Ne jamais appeler la vraie API France Travail ni le webhook Discord sans l'accord explicite
  de l'utilisateur.
- Docs, logs et messages en français ; commits conventionnels (`feat:`, `fix:`, `ci:`...), un par
  étape logique.
- Sous Windows, le projet vit dans WSL (`~/jobwatch`) : lancer les commandes dans Ubuntu.
