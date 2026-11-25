"""jobwatch : veille quotidienne des offres France Travail (CDI DevOps / Cloud / Run)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("jobwatch")
except PackageNotFoundError:  # pragma: no cover - exécution depuis les sources sans installation
    __version__ = "0.0.0"

__all__ = ["__version__"]
