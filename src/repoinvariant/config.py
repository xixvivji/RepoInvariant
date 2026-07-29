"""Configuration loading and repository-local path discovery."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from repoinvariant.filesystem import (
    MAX_CONFIG_BYTES,
    MAX_SCAN_FILES,
    contained_path,
    read_limited_text,
)

CONFIG_NAME = ".repoinvariant.yml"

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
        "ignore": [
            "CI",
            "HOME",
            "PATH",
            "PWD",
            "SHELL",
            "USER",
            "GH_TOKEN",
            "GITHUB_*",
            "RUNNER_*",
        ],
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
        "requirements_mode": "definitions",
        "ignore": [],
    },
    "rules": {
        "ENV001": "error",
        "ENV002": "warning",
        "ENV003": "warning",
        "TRACE001": "error",
        "TRACE002": "error",
        "TRACE003": "error",
        "TRACE004": "warning",
    },
}

DEFAULT_CONFIG_TEXT = """# RepoInvariant compares contracts across files.
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
    - GH_TOKEN
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
  # Count canonical definitions only; use "mentions" for legacy repositories.
  requirements_mode: definitions
  ignore: []

# Downgrade noisy checks while adopting RepoInvariant, then tighten them over time.
rules:
  ENV001: error
  ENV002: warning
  ENV003: warning
  TRACE001: error
  TRACE002: error
  TRACE003: error
  TRACE004: warning
"""


class ConfigError(ValueError):
    """Raised when a repository configuration cannot be interpreted safely."""


_TOP_LEVEL_KEYS = frozenset({"version", "env", "features", "rules"})
_ENV_KEYS = frozenset({"contracts", "compose", "kubernetes", "workflows", "spring", "ignore"})
_FEATURE_KEYS = frozenset(
    {
        "requirements",
        "specifications",
        "tests",
        "id_pattern",
        "openapi_extension",
        "requirements_mode",
        "ignore",
    }
)
_RULE_KEYS = frozenset(
    {"ENV001", "ENV002", "ENV003", "TRACE001", "TRACE002", "TRACE003", "TRACE004"}
)
_RULE_VALUES = frozenset({"error", "warning", "off"})
_OPENAPI_EXTENSION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", re.ASCII)
_PATH_LISTS = {
    "env": ("contracts", "compose", "kubernetes", "workflows", "spring"),
    "features": ("requirements", "specifications", "tests", "ignore"),
}


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _normalize_yaml_scalars(config: dict[str, Any]) -> None:
    """Normalize YAML 1.1's unquoted ``off`` boolean to the policy spelling."""

    rules = config.get("rules")
    if isinstance(rules, dict):
        for code, value in rules.items():
            if value is False:
                rules[code] = "off"


def _validate_structure(
    value: Any,
    *,
    seen: set[int] | None = None,
    active: set[int] | None = None,
    depth: int = 0,
) -> None:
    """Reject recursive or excessively nested YAML before merging defaults."""

    if depth > 64:
        raise ConfigError("configuration nesting exceeds 64 levels")
    if not isinstance(value, (Mapping, list)):
        return
    seen = seen if seen is not None else set()
    active = active if active is not None else set()
    identity = id(value)
    if identity in active:
        raise ConfigError("configuration must not contain recursive YAML aliases")
    if identity in seen:
        return
    if len(seen) > 20_000:
        raise ConfigError("configuration contains too many nodes")
    seen.add(identity)
    active.add(identity)
    try:
        children = (*value.keys(), *value.values()) if isinstance(value, Mapping) else value
        for child in children:
            _validate_structure(child, seen=seen, active=active, depth=depth + 1)
    finally:
        active.remove(identity)


def _string_list(section: Mapping[str, Any], key: str) -> None:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and bool(item.strip()) for item in value
    ):
        raise ConfigError(f"'{key}' must be a list of non-empty strings")
    if len(value) > 128:
        raise ConfigError(f"'{key}' must not contain more than 128 entries")


def _reject_unknown_keys(section: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(
        (key for key in section if not isinstance(key, str) or key not in allowed),
        key=repr,
    )
    if unknown:
        raise ConfigError(f"unknown {label} key(s): {', '.join(map(repr, unknown))}")


def _validate_relative_pattern(pattern: str, label: str) -> None:
    if len(pattern) > 512:
        raise ConfigError(f"'{label}' entries must not exceed 512 characters")
    normalized = pattern.replace("\\", "/").removeprefix("./")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or (len(normalized) >= 3 and normalized[1:3] == ":/")
        or ".." in path.parts
    ):
        raise ConfigError(
            f"'{label}' entries must be repository-relative and must not contain '..'"
        )


def validate_config(config: Mapping[str, Any]) -> None:
    _reject_unknown_keys(config, _TOP_LEVEL_KEYS, "top-level")
    if type(config.get("version")) is not int or config.get("version") != 1:
        raise ConfigError("only configuration version 1 is supported")

    env = config.get("env")
    features = config.get("features")
    if not isinstance(env, Mapping):
        raise ConfigError("'env' must be a mapping")
    if not isinstance(features, Mapping):
        raise ConfigError("'features' must be a mapping")

    _reject_unknown_keys(env, _ENV_KEYS, "env")
    _reject_unknown_keys(features, _FEATURE_KEYS, "features")

    for key in ("contracts", "compose", "kubernetes", "workflows", "spring", "ignore"):
        _string_list(env, key)
    for key in ("requirements", "specifications", "tests", "ignore"):
        _string_list(features, key)
    for key in ("id_pattern", "openapi_extension"):
        if not isinstance(features.get(key), str) or not features[key]:
            raise ConfigError(f"'features.{key}' must be a non-empty string")
    if len(features["id_pattern"]) > 512:
        raise ConfigError("'features.id_pattern' must not exceed 512 characters")
    if not _OPENAPI_EXTENSION_RE.fullmatch(features["openapi_extension"]):
        raise ConfigError(
            "'features.openapi_extension' must be a safe 1-64 character identifier"
        )
    if features.get("requirements_mode") not in {"definitions", "mentions"}:
        raise ConfigError("'features.requirements_mode' must be 'definitions' or 'mentions'")

    for section_name, keys in _PATH_LISTS.items():
        section = env if section_name == "env" else features
        for key in keys:
            for pattern in section[key]:
                _validate_relative_pattern(pattern, f"{section_name}.{key}")

    rules = config.get("rules")
    if not isinstance(rules, Mapping):
        raise ConfigError("'rules' must be a mapping")
    _reject_unknown_keys(rules, _RULE_KEYS, "rules")
    for code, value in rules.items():
        if value not in _RULE_VALUES:
            raise ConfigError(f"'rules.{code}' must be 'error', 'warning', or 'off'")


def load_config(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Load a versioned config, falling back to safe convention-based defaults."""

    root = root.resolve()
    requested_path = config_path or Path(CONFIG_NAME)
    candidate = requested_path if requested_path.is_absolute() else root / requested_path
    if not candidate.exists() and not candidate.is_symlink():
        if config_path is not None:
            raise ConfigError(f"configuration file does not exist: {candidate}")
        config = deepcopy(DEFAULT_CONFIG)
        validate_config(config)
        return config

    try:
        path = contained_path(root, candidate, label="configuration file")
        text = read_limited_text(path, root=root, max_bytes=MAX_CONFIG_BYTES)
        raw = yaml.load(text, Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, ValueError, RecursionError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {candidate}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path} must contain a YAML mapping")
    _validate_structure(raw)
    try:
        config = _merge(DEFAULT_CONFIG, raw)
    except RecursionError as exc:
        raise ConfigError("configuration nesting is too deep") from exc
    _normalize_yaml_scalars(config)
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
            _validate_relative_pattern(pattern, "path pattern")
            candidates = root.glob(pattern)
        except (OSError, ValueError, NotImplementedError) as exc:
            raise ConfigError(f"invalid path pattern {pattern!r}: {exc}") from exc
        for candidate in candidates:
            if candidate.is_symlink():
                raise ConfigError(f"configured file must not be a symbolic link: {candidate}")
            try:
                relative = candidate.resolve().relative_to(root)
            except (OSError, ValueError):
                continue
            if any(part in excluded_parts for part in relative.parts):
                continue
            if candidate.is_file():
                found.add(candidate.resolve())
                if len(found) > MAX_SCAN_FILES:
                    raise ConfigError(f"file discovery exceeds {MAX_SCAN_FILES} files")
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())
