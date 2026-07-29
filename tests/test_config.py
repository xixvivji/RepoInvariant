from pathlib import Path

import pytest

from repotruth.config import ConfigError, discover_files, load_config


def test_load_config_merges_defaults_with_repository_config(tmp_path: Path) -> None:
    (tmp_path / ".repotruth.yml").write_text(
        """version: 1
env:
  contracts: [config/example.env]
  ignore: [LOCAL_*]
features:
  requirements: []
""",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config["env"]["contracts"] == ["config/example.env"]
    assert config["env"]["ignore"] == ["LOCAL_*"]
    assert config["env"]["compose"]
    assert config["features"]["requirements"] == []
    assert config["features"]["openapi_extension"] == "x-feature-id"


def test_load_config_rejects_unknown_version(tmp_path: Path) -> None:
    (tmp_path / ".repotruth.yml").write_text("version: 99\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="version 1"):
        load_config(tmp_path)


@pytest.mark.parametrize("version", ["true", "1.0", "'1'"])
def test_load_config_requires_integer_version_one(tmp_path: Path, version: str) -> None:
    (tmp_path / ".repotruth.yml").write_text(f"version: {version}\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="version 1"):
        load_config(tmp_path)


def test_load_config_rejects_explicit_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path, Path("config/repotruth.yml"))


def test_discover_files_is_stable_and_excludes_virtualenv(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "docs" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.md").write_text("no", encoding="utf-8")

    paths = discover_files(tmp_path, ["**/*.md"])

    assert [path.relative_to(tmp_path).as_posix() for path in paths] == ["docs/a.md", "docs/b.md"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("version: 1\nenv:\n  compsoe: []\n", "unknown env key"),
        ("version: 1\nversion: 1\n", "duplicate key"),
        (
            "version: 1\nfeatures:\n  requirements: [/tmp/requirements.md]\n",
            "repository-relative",
        ),
        (
            "version: 1\nfeatures:\n  tests: [../tests/**/*]\n",
            "must not contain '..'",
        ),
        ("version: 1\nrules:\n  TRACE003: maybe\n", "error.*warning.*off"),
        (
            "version: 1\nfeatures:\n  openapi_extension: 'bad\\nvalue'\n",
            "safe 1-64 character identifier",
        ),
    ],
)
def test_load_config_rejects_unsafe_or_ambiguous_configuration(
    tmp_path: Path, content: str, message: str
) -> None:
    (tmp_path / ".repotruth.yml").write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(tmp_path)


def test_load_config_rejects_symlink_outside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.yml"
    outside.write_text("version: 1\n", encoding="utf-8")
    (root / ".repotruth.yml").symlink_to(outside)

    with pytest.raises(ConfigError, match="symbolic link"):
        load_config(root)


def test_load_config_rejects_recursive_yaml_alias(tmp_path: Path) -> None:
    (tmp_path / ".repotruth.yml").write_text(
        "version: 1\nenv: &env\n  contracts: []\n  compose: []\n  kubernetes: []\n"
        "  workflows: []\n  spring: []\n  ignore: [*env]\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="recursive YAML aliases"):
        load_config(tmp_path)


def test_load_config_wraps_parser_recursion_as_config_error(tmp_path: Path) -> None:
    nested = "[" * 500 + "0" + "]" * 500
    (tmp_path / ".repotruth.yml").write_text(
        f"version: 1\nfeatures:\n  requirements: {nested}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path)


def test_rules_and_requirement_membership_mode_merge_with_defaults(tmp_path: Path) -> None:
    (tmp_path / ".repotruth.yml").write_text(
        "version: 1\nfeatures:\n  requirements_mode: mentions\nrules:\n  TRACE003: off\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config["features"]["requirements_mode"] == "mentions"
    assert config["rules"]["TRACE003"] == "off"
    assert config["rules"]["ENV001"] == "error"
