"""Exceptions métier, chacune associée à un code de sortie de la CLI.

| code | signification                                   |
|------|-------------------------------------------------|
| 0    | succès (y compris échec partiel des requêtes)   |
| 1    | toutes les requêtes de recherche ont échoué     |
| 2    | configuration absente ou invalide / usage       |
| 3    | authentification France Travail refusée         |
| 4    | envoi Discord en échec                          |
"""


class JobwatchError(Exception):
    """Base des erreurs jobwatch."""

    exit_code: int = 1


class ConfigError(JobwatchError):
    """Configuration (fichier YAML ou variables d'environnement) absente ou invalide."""

    exit_code = 2


class FranceTravailError(JobwatchError):
    """Erreur générique de l'API France Travail."""


class AuthError(FranceTravailError):
    """Le token OAuth2 n'a pas pu être obtenu."""

    exit_code = 3


class SearchError(FranceTravailError):
    """Une requête de recherche a échoué après les retries."""


class AllQueriesFailedError(JobwatchError):
    """Aucune requête de recherche n'a abouti pendant le run."""

    exit_code = 1


class NotifyError(JobwatchError):
    """Le digest n'a pas pu être envoyé sur Discord."""

    exit_code = 4
