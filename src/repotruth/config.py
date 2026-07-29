"""Configuration loading and repository-local path discovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

CONFIG_NAME = ".repotruth.yml"

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "env": {
        "contracts": [".env.example"],
        "compose": ["compose*.yml", "compose*.yaml", "docker-compose*.yml", "docker-compose*.yaml"],
        "kubernetes": [
            "k8s/**/*.yml",
            "k8s/**/*.yaml",
            "kubernetes/**/*.yml",
            "kubernetes/**/*.yaml",
        ],
        "workflows": [".github/workflows/*.yml", ".github/workflows/*.yaml"],
        "spring": [
            "src/main/resources/application*.yml",
            "src/main/resources/application*.yaml",
            "src/main/resources/application*.properties",
        ],
        "ignore": ["CI", "HOME", "PATH", "PWD", "SHELL", "USER", "GITHUB_*", "RUNNER_*"],
    },
    "features": {
        "requirements": ["docs/**/*.md"],
        "specifications": [
            "openapi*.yml",
            "openapi*.yaml",
            "openapi*.json",
            "docs/openapi*.yml",
            "docs/openapi*.yaml",
            "docs/openapi*.json",
        ],
        "tests": ["tests/**/*", "src/test/**/*"],
        "id_pattern": r"\bREQ-[A-Z0-9][A-Z0-9-]*\b",
        "openapi_extension": "x-feature-id",
        "ignore": [],
    },
}

DEFAULT_CONFIG_TEXT = """# RepoTruth compares contracts across files.
# Keep the first version intentionally narrow.
version: 1

env:
  contracts:
    - .env.example
  compose:
    - compose*.yml
    - compose*.yaml
    - docker-compose*.yml
    - docker-compose*.yaml
  kubernetes:
    - k8s/**/*.yml
    - k8s/**/*.yaml
    - kubernetes/**/*.yml
    - kubernetes/**/*.yaml
  workflows:
    - .github/workflows/*.yml
    - .github/workflows/*.yaml
  spring:
    - src/main/resources/application*.yml
    - src/main/resources/application*.yaml
    - src/main/resources/application*.properties
  ignore:
    - CI
    - HOME
    - PATH
    - PWD
    - SHELL
    - USER
    - GITHUB_*
    - RUNNER_*

features:
  requirements:
    - docs/**/*.md
  specifications:
    - openapi*.yml
    - openapi*.yaml
    - openapi*.json
    - docs/openapi*.yml
    - docs/openapi*.yaml
    - docs/openapi*.json
  tests:
    - tests/**/*
    - src/test/**/*
  id_pattern: '\\bREQ-[A-Z0-9][A-Z0-9-]*\\b'
  openapi_extension: x-feature-id
  ignore: []
"""


class ConfigError(ValueError):
    """Raised when a repository configuration cannot be interpreted safely."""


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _string_list(section: Mapping[str, Any], key: str) -> None:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"'{key}' must be a list of strings")


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("version") != 1:
        raise ConfigError("only configuration version 1 is supported")

    env = config.get("env")
    features = config.get("features")
    if not isinstance(env, Mapping):
        raise ConfigError("'env' must be a mapping")
    if not isinstance(features, Mapping):
        raise ConfigError("'features' must be a mapping")

    for key in ("contracts", "compose", "kubernetes", "workflows", "spring", "ignore"):
        _string_list(env, key)
    for key in ("requirements", "specifications", "tests", "ignore"):
        _string_list(features, key)
    for key in ("id_pattern", "openapi_extension"):
        if not isinstance(features.get(key), str) or not features[key]:
            raise ConfigError(f"'features.{key}' must be a non-empty string")


def load_config(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Load a versioned config, falling back to safe convention-based defaults."""

    root = root.resolve()
    path = config_path or root / CONFIG_NAME
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        config = deepcopy(DEFAULT_CONFIG)
        validate_config(config)
        return config

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} must contain a YAML mapping")
    config = _merge(DEFAULT_CONFIG, raw)
    validate_config(config)
    return config


def discover_files(root: Path, patterns: Sequence[str]) -> list[Path]:
    """Expand repository-relative globs and return stable, safe regular files."""

    root = root.resolve()
    found: set[Path] = set()
    excluded_parts = {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
    }
    for pattern in patterns:
        try:
            candidates = root.glob(pattern)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"invalid path pattern {pattern!r}: {exc}") from exc
        for candidate in candidates:
            try:
                relative = candidate.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            if any(part in excluded_parts for part in relative.parts):
                continue
            if candidate.is_file():
                found.add(candidate.resolve())
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())
