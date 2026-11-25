from pathlib import Path

import pytest
import yaml

from jobwatch.config import AppConfig, load_config
from jobwatch.errors import ConfigError

from .conftest import ROOT


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _shipped() -> dict[str, object]:
    loaded = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_shipped_config_is_valid(app_config: AppConfig) -> None:
    search = app_config.search
    assert search.contract_type == "CDI"
    assert search.departments == {
        "59": "Lille",
        "75": "Paris",
        "92": "Paris",
        "69": "Lyon",
        "13": "Aix-Marseille",
    }
    assert "ingénieur d'exploitation" in search.keywords
    assert len(search.queries()) == len(search.keywords) * len(search.departments) == 45
    assert app_config.notify.min_score == 40
    assert app_config.scoring.stack.max_bonus == 24
    assert app_config.scoring.stack.aliases["kubernetes"] == ["k8s"]


def test_zone_falls_back_to_code(app_config: AppConfig) -> None:
    assert app_config.search.zone("92") == "Paris"
    assert app_config.search.zone("33") == "33"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="introuvable"):
        load_config(tmp_path / "absent.yaml")


def test_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("search: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML invalide"):
        load_config(path)


def test_root_must_be_mapping(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="objet YAML"):
        load_config(_write(tmp_path, ["a", "b"]))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    data = _shipped()
    data["notify"] = {"min_scor": 10}
    with pytest.raises(ConfigError, match=r"notify\.min_scor"):
        load_config(_write(tmp_path, data))


@pytest.mark.parametrize(
    ("section", "patch", "message"),
    [
        ("search", {"keywords": ["x"]}, "moins de 2 caractères"),
        ("search", {"departments": {"7": "Ardèche"}}, "code département invalide"),
        ("search", {"departments": {}}, "at least 1 item"),
    ],
)
def test_invalid_search_section(
    tmp_path: Path, section: str, patch: dict[str, object], message: str
) -> None:
    data = _shipped()
    current = data[section]
    assert isinstance(current, dict)
    data[section] = {**current, **patch}
    with pytest.raises(ConfigError, match=message):
        load_config(_write(tmp_path, data))


def test_alias_for_unknown_tech(tmp_path: Path) -> None:
    data = _shipped()
    scoring = data["scoring"]
    assert isinstance(scoring, dict)
    scoring["stack"]["aliases"] = {"nomad": ["hashicorp nomad"]}
    with pytest.raises(ConfigError, match="nomad"):
        load_config(_write(tmp_path, data))


def test_terms_are_cleaned_and_deduplicated(tmp_path: Path) -> None:
    data = _shipped()
    search = data["search"]
    assert isinstance(search, dict)
    search["keywords"] = ["devops", "  DevOps ", "platform   engineer"]
    config = load_config(_write(tmp_path, data))
    assert config.search.keywords == ["devops", "platform engineer"]
