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


def test_discover_files_is_stable_and_excludes_virtualenv(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "docs" / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "hidden.md").write_text("no", encoding="utf-8")

    paths = discover_files(tmp_path, ["**/*.md"])

    assert [path.relative_to(tmp_path).as_posix() for path in paths] == ["docs/a.md", "docs/b.md"]
