from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from repoinvariant.config import (
    DEFAULT_CONFIG,
    DEFAULT_CONFIG_TEXT,
    VERSION_JAVA_DEFAULTS,
    VERSION_RULE_DEFAULTS,
    ConfigError,
    load_config,
)

ROOT = Path(__file__).parents[1]
SCHEMA_PATH = ROOT / "schemas" / "repoinvariant-config-v1.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)


def _write_raw_config(root: Path, raw: Any) -> None:
    (root / ".repoinvariant.yml").write_text(
        yaml.safe_dump(raw, sort_keys=False),
        encoding="utf-8",
    )


def test_config_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA["$id"].endswith("/schemas/repoinvariant-config-v1.schema.json")


def test_schema_keys_and_default_annotations_track_the_runtime_contract() -> None:
    assert set(SCHEMA["properties"]) == {"version", "env", "features", "versions", "rules"}

    env = SCHEMA["$defs"]["environment"]["properties"]
    features = SCHEMA["$defs"]["features"]["properties"]
    java = SCHEMA["$defs"]["javaVersion"]["properties"]
    rules = SCHEMA["$defs"]["rules"]["properties"]

    assert set(env) == set(DEFAULT_CONFIG["env"])
    assert set(features) == set(DEFAULT_CONFIG["features"])
    assert set(java) == {*VERSION_JAVA_DEFAULTS, "expected"}
    assert set(rules) == {*DEFAULT_CONFIG["rules"], *VERSION_RULE_DEFAULTS}

    for key, value in DEFAULT_CONFIG["env"].items():
        assert env[key]["default"] == value
    for key, value in DEFAULT_CONFIG["features"].items():
        assert features[key]["default"] == value
    for key, value in VERSION_JAVA_DEFAULTS.items():
        assert java[key]["default"] == value
    for key, value in DEFAULT_CONFIG["rules"].items():
        assert rules[key]["default"] == value
    for key in VERSION_RULE_DEFAULTS:
        assert "default" not in rules[key]


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"version": 1},
        {"env": {"contracts": [], "ignore": ["X" * 600]}},
        {"features": {"requirements": [], "id_pattern": " "}},
        {"versions": {"java": {"expected": "1"}}},
        {
            "versions": {
                "java": {
                    "expected": "999",
                    "required": ["gradle", "dockerfiles", "compose", "workflows", "docs"],
                }
            }
        },
        {"rules": {"ENV001": "off", "TRACE004": False, "VER003": "warning"}},
    ],
)
def test_schema_accepts_every_supported_partial_configuration(
    tmp_path: Path, raw: dict[str, Any]
) -> None:
    VALIDATOR.validate(raw)
    _write_raw_config(tmp_path, raw)

    load_config(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param([], id="root-array"),
        pytest.param({"unknown": True}, id="unknown-top-level-key"),
        pytest.param({"version": True}, id="boolean-version"),
        pytest.param({"version": 2}, id="unsupported-version"),
        pytest.param({"env": {"unknown": []}}, id="unknown-env-key"),
        pytest.param({"env": {"contracts": "config.env"}}, id="non-list-source"),
        pytest.param({"env": {"ignore": [" "]}}, id="blank-ignore"),
        pytest.param({"env": {"contracts": ["item"] * 129}}, id="too-many-patterns"),
        pytest.param({"features": {"requirements": ["/tmp/requirements.md"]}}, id="absolute"),
        pytest.param({"features": {"tests": ["../tests"]}}, id="parent-component"),
        pytest.param({"features": {"tests": ["./"]}}, id="empty-normalized-path"),
        pytest.param({"features": {"tests": ["C:\\outside"]}}, id="drive-path"),
        pytest.param({"features": {"tests": ["\n:/outside"]}}, id="newline-drive-path"),
        pytest.param({"features": {"tests": ["x" * 513]}}, id="long-path"),
        pytest.param({"features": {"id_pattern": ""}}, id="empty-id-pattern"),
        pytest.param({"features": {"id_pattern": "x" * 513}}, id="long-id-pattern"),
        pytest.param({"features": {"openapi_extension": "bad value"}}, id="unsafe-extension"),
        pytest.param({"features": {"requirements_mode": "all"}}, id="unknown-mode"),
        pytest.param({"versions": None}, id="null-versions"),
        pytest.param({"versions": {}}, id="missing-java"),
        pytest.param({"versions": {"java": {}}}, id="missing-expected"),
        pytest.param(
            {"versions": {"java": {"expected": 21}}}, id="unquoted-java-major"
        ),
        pytest.param(
            {"versions": {"java": {"expected": "021"}}}, id="noncanonical-java-major"
        ),
        pytest.param(
            {"versions": {"java": {"expected": "21", "required": ["docs", "docs"]}}},
            id="duplicate-required-source",
        ),
        pytest.param(
            {"versions": {"java": {"expected": "21", "required": ["maven"]}}},
            id="unknown-required-source",
        ),
        pytest.param({"rules": {"UNKNOWN": "error"}}, id="unknown-rule"),
        pytest.param({"rules": {"ENV001": "maybe"}}, id="unknown-severity"),
        pytest.param({"rules": {"ENV001": True}}, id="true-severity"),
    ],
)
def test_schema_and_runtime_reject_invalid_raw_configuration(
    tmp_path: Path, raw: Any
) -> None:
    assert list(VALIDATOR.iter_errors(raw))
    _write_raw_config(tmp_path, raw)

    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_starter_repository_and_readme_configs_validate_against_the_schema(
    tmp_path: Path,
) -> None:
    for text in (DEFAULT_CONFIG_TEXT, (ROOT / ".repoinvariant.yml").read_text(encoding="utf-8")):
        raw = yaml.safe_load(text)
        VALIDATOR.validate(raw)

    modeline = f"# yaml-language-server: $schema={SCHEMA['$id']}"
    assert DEFAULT_CONFIG_TEXT.splitlines()[0] == modeline
    assert (ROOT / ".repoinvariant.yml").read_text(encoding="utf-8").splitlines()[0] == modeline

    readme_configuration = (ROOT / "README.md").read_text(encoding="utf-8").split(
        "## Configuration", maxsplit=1
    )[1]
    yaml_block = readme_configuration.split("```yaml", maxsplit=1)[1].split("```", maxsplit=1)[0]
    assert yaml_block.strip().splitlines()[0] == modeline
    readme_raw = yaml.safe_load(yaml_block)
    VALIDATOR.validate(readme_raw)
    for source in ("gradle", "dockerfiles", "compose", "workflows", "docs"):
        assert readme_raw["versions"]["java"][source] == VERSION_JAVA_DEFAULTS[source]

    (tmp_path / ".repoinvariant.yml").write_text(yaml_block, encoding="utf-8")
    load_config(tmp_path)


def test_documented_schema_limitations_remain_runtime_checked(tmp_path: Path) -> None:
    # JSON Schema treats 1.0 as an integer value, while the YAML loader deliberately requires
    # the exact Python integer scalar. An explicit null document is accepted by the legacy loader
    # as an empty configuration but rejected by the editor schema as likely accidental.
    assert not list(VALIDATOR.iter_errors({"version": 1.0}))
    assert list(VALIDATOR.iter_errors(None))

    (tmp_path / ".repoinvariant.yml").write_text("version: 1.0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="version 1"):
        load_config(tmp_path)

    merge_key_config = "version: 1\nfeatures:\n  <<:\n    requirements: []\n"
    VALIDATOR.validate(yaml.safe_load(merge_key_config))
    (tmp_path / ".repoinvariant.yml").write_text(merge_key_config, encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path)

    (tmp_path / ".repoinvariant.yml").write_text("null\n", encoding="utf-8")
    assert load_config(tmp_path) == DEFAULT_CONFIG
