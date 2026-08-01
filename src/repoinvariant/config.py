"""Configuration loading and repository-local path discovery."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from repoinvariant.diagnostics import SourceDiagnostics
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

# Optional scanners deliberately live outside ``DEFAULT_CONFIG``.  Adding them there would
# change the effective configuration (and therefore adoption-baseline scope digests) for every
# existing v0.3 repository, even when the scanner is not configured.
VERSION_JAVA_DEFAULTS: dict[str, Any] = {
    "gradle": ["**/build.gradle", "**/build.gradle.kts"],
    "maven": ["**/pom.xml"],
    "version_files": ["**/.java-version"],
    "dockerfiles": ["**/Dockerfile", "**/Dockerfile.*"],
    "compose": [
        "**/compose*.yml",
        "**/compose*.yaml",
        "**/docker-compose*.yml",
        "**/docker-compose*.yaml",
    ],
    "workflows": [".github/workflows/*.yml", ".github/workflows/*.yaml"],
    "docs": ["README.md", "docs/**/*.md"],
    "ignore": [],
    "required": [],
}

VERSION_RULE_DEFAULTS: dict[str, str] = {
    "VER001": "error",
    "VER002": "warning",
    "VER003": "warning",
}

DEFAULT_CONFIG_TEXT = """# yaml-language-server: $schema=https://raw.githubusercontent.com/xixvivji/RepoInvariant/main/schemas/repoinvariant-config-v1.schema.json
# RepoInvariant compares contracts across files.
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

# Opt in to a Java major-version contract when the repository has one canonical target:
# versions:
#   java:
#     expected: "21"
#     required: [gradle, workflows, docs]
# VER001/VER002/VER003 use error/warning/warning defaults when this section is enabled.
"""


class ConfigError(ValueError):
    """Raised when a repository configuration cannot be interpreted safely."""


_TOP_LEVEL_KEYS = frozenset({"version", "env", "features", "versions", "plugins", "rules"})
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
_VERSION_KEYS = frozenset({"java"})
_JAVA_VERSION_KEYS = frozenset(
    {
        "expected",
        "gradle",
        "maven",
        "version_files",
        "dockerfiles",
        "compose",
        "workflows",
        "docs",
        "ignore",
        "required",
    }
)
_VERSION_SOURCES = frozenset(
    {"gradle", "maven", "version_files", "dockerfiles", "compose", "workflows", "docs"}
)
_JAVA_MAJOR_RE = re.compile(r"^(?:[1-9][0-9]{0,2})$", re.ASCII)
_PLUGIN_ID_RE = re.compile(
    r"^[a-z][a-z0-9]{0,31}(?:[._-][a-z0-9][a-z0-9]{0,31})*$",
    re.ASCII,
)
_PLUGIN_RULE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,31}$", re.ASCII)
_PLUGIN_SETTING_KEYS = frozenset({"config", "rules"})
_RULE_KEYS = frozenset(
    {
        "ENV001",
        "ENV002",
        "ENV003",
        "TRACE001",
        "TRACE002",
        "TRACE003",
        "TRACE004",
        "VER001",
        "VER002",
        "VER003",
    }
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


def _merge(
    base: dict[str, Any],
    override: Mapping[str, Any],
    *,
    memo: dict[int, Any] | None = None,
) -> dict[str, Any]:
    memo = memo if memo is not None else {}
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value, memo=memo)
        else:
            result[key] = deepcopy(value, memo)
    return result


def apply_optional_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults for explicitly enabled scanners without enabling them implicitly."""

    versions = config.get("versions")
    if not isinstance(versions, Mapping):
        return config
    java = versions.get("java")
    if not isinstance(java, Mapping):
        return config

    expanded_versions = dict(versions)
    expanded_versions["java"] = _merge(VERSION_JAVA_DEFAULTS, java)
    config["versions"] = expanded_versions
    rules = config.get("rules")
    if isinstance(rules, dict):
        for code, severity in VERSION_RULE_DEFAULTS.items():
            rules.setdefault(code, severity)
    return config


def _normalize_yaml_scalars(config: dict[str, Any]) -> None:
    """Normalize YAML 1.1's unquoted ``off`` boolean to the policy spelling."""

    rules = config.get("rules")
    if isinstance(rules, dict):
        for code, value in rules.items():
            if value is False:
                rules[code] = "off"
    plugins = config.get("plugins")
    if isinstance(plugins, dict):
        for settings in plugins.values():
            if not isinstance(settings, dict):
                continue
            plugin_rules = settings.get("rules")
            if isinstance(plugin_rules, dict):
                for code, value in plugin_rules.items():
                    if value is False:
                        plugin_rules[code] = "off"


def _validate_structure(
    value: Any,
    *,
    seen: set[int] | None = None,
    active: set[int] | None = None,
    budget: list[int] | None = None,
    depth: int = 0,
) -> None:
    """Reject recursive or excessively nested YAML before merging defaults."""

    budget = budget if budget is not None else [0]
    budget[0] += 1
    if budget[0] > 20_000:
        raise ConfigError("configuration contains too many nodes or alias references")
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
    seen.add(identity)
    active.add(identity)
    try:
        children = (*value.keys(), *value.values()) if isinstance(value, Mapping) else value
        for child in children:
            _validate_structure(
                child,
                seen=seen,
                active=active,
                budget=budget,
                depth=depth + 1,
            )
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


def _preflight_override_keys(config: Mapping[str, Any]) -> None:
    """Reject unknown YAML branches before a memo-breaking defaults merge."""

    _reject_unknown_keys(config, _TOP_LEVEL_KEYS, "top-level")
    for name, allowed in (("env", _ENV_KEYS), ("features", _FEATURE_KEYS), ("rules", _RULE_KEYS)):
        section = config.get(name)
        if isinstance(section, Mapping):
            _reject_unknown_keys(section, allowed, name)
    versions = config.get("versions")
    if isinstance(versions, Mapping):
        _reject_unknown_keys(versions, _VERSION_KEYS, "versions")
        java = versions.get("java")
        if isinstance(java, Mapping):
            _reject_unknown_keys(java, _JAVA_VERSION_KEYS, "versions.java")
    plugins = config.get("plugins")
    if isinstance(plugins, Mapping):
        for plugin_id, settings in plugins.items():
            if isinstance(plugin_id, str) and isinstance(settings, Mapping):
                _reject_unknown_keys(settings, _PLUGIN_SETTING_KEYS, f"plugins.{plugin_id}")


def _validate_plugin_config_value(
    value: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> None:
    """Validate the data-only configuration passed to an explicitly selected plugin."""

    budget = budget if budget is not None else [0]
    budget[0] += 1
    if budget[0] > 10_000:
        raise ConfigError("plugin configuration contains too many values")
    if depth > 32:
        raise ConfigError("plugin configuration nesting exceeds 32 levels")
    if value is None or type(value) in {bool, int, float}:
        if isinstance(value, float) and not (-float("inf") < value < float("inf")):
            raise ConfigError("plugin configuration numbers must be finite")
        return
    if isinstance(value, str):
        if len(value) > 4_096:
            raise ConfigError("plugin configuration strings must not exceed 4096 characters")
        return
    if isinstance(value, list):
        if len(value) > 1_024:
            raise ConfigError("plugin configuration lists must not exceed 1024 items")
        for item in value:
            _validate_plugin_config_value(item, depth=depth + 1, budget=budget)
        return
    if isinstance(value, Mapping):
        if len(value) > 1_024:
            raise ConfigError("plugin configuration mappings must not exceed 1024 entries")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ConfigError(
                    "plugin configuration keys must be non-empty strings up to 128 characters"
                )
            _validate_plugin_config_value(item, depth=depth + 1, budget=budget)
        return
    raise ConfigError("plugin configuration must contain only JSON-compatible values")


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

    if "versions" in config:
        versions = config["versions"]
        if not isinstance(versions, Mapping):
            raise ConfigError("'versions' must be a mapping")
        _reject_unknown_keys(versions, _VERSION_KEYS, "versions")
        java = versions.get("java")
        if not isinstance(java, Mapping):
            raise ConfigError("'versions.java' must be a mapping")
        _reject_unknown_keys(java, _JAVA_VERSION_KEYS, "versions.java")

        expected = java.get("expected")
        if not isinstance(expected, str) or not _JAVA_MAJOR_RE.fullmatch(expected):
            raise ConfigError(
                "'versions.java.expected' must be a quoted canonical major from '1' to '999'"
        )
        for key in (
            "gradle",
            "maven",
            "version_files",
            "dockerfiles",
            "compose",
            "workflows",
            "docs",
            "ignore",
        ):
            _string_list(java, key)
            for pattern in java.get(key, []):
                _validate_relative_pattern(pattern, f"versions.java.{key}")

        required = java.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ConfigError("'versions.java.required' must be a list of source names")
        if len(required) != len(set(required)):
            raise ConfigError("'versions.java.required' must not contain duplicate source names")
        unknown_sources = sorted(set(required) - _VERSION_SOURCES)
        if unknown_sources:
            raise ConfigError(
                "'versions.java.required' contains unknown source(s): "
                + ", ".join(map(repr, unknown_sources))
            )

    if "plugins" in config:
        plugins = config["plugins"]
        if not isinstance(plugins, Mapping):
            raise ConfigError("'plugins' must be a mapping")
        if len(plugins) > 32:
            raise ConfigError("'plugins' must not contain more than 32 entries")
        for plugin_id, settings in plugins.items():
            if (
                not isinstance(plugin_id, str)
                or len(plugin_id) > 64
                or not _PLUGIN_ID_RE.fullmatch(plugin_id)
            ):
                raise ConfigError("plugin IDs must be safe lowercase identifiers")
            if not isinstance(settings, Mapping):
                raise ConfigError(f"'plugins.{plugin_id}' must be a mapping")
            _reject_unknown_keys(settings, _PLUGIN_SETTING_KEYS, f"plugins.{plugin_id}")
            plugin_config = settings.get("config", {})
            if not isinstance(plugin_config, Mapping):
                raise ConfigError(f"'plugins.{plugin_id}.config' must be a mapping")
            _validate_plugin_config_value(plugin_config)
            plugin_rules = settings.get("rules", {})
            if not isinstance(plugin_rules, Mapping):
                raise ConfigError(f"'plugins.{plugin_id}.rules' must be a mapping")
            if len(plugin_rules) > 128:
                raise ConfigError(
                    f"'plugins.{plugin_id}.rules' must not contain more than 128 entries"
                )
            for code, value in plugin_rules.items():
                if not isinstance(code, str) or not _PLUGIN_RULE_RE.fullmatch(code):
                    raise ConfigError(f"'plugins.{plugin_id}.rules' contains an invalid rule code")
                if not isinstance(value, str) or value not in _RULE_VALUES:
                    raise ConfigError(
                        f"'plugins.{plugin_id}.rules.{code}' must be "
                        "'error', 'warning', or 'off'"
                    )

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
        if not isinstance(value, str) or value not in _RULE_VALUES:
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
    _preflight_override_keys(raw)
    try:
        config = apply_optional_defaults(_merge(DEFAULT_CONFIG, raw))
    except RecursionError as exc:
        raise ConfigError("configuration nesting is too deep") from exc
    _normalize_yaml_scalars(config)
    validate_config(config)
    return config


def discover_files(
    root: Path,
    patterns: Sequence[str],
    *,
    diagnostics: SourceDiagnostics | None = None,
) -> list[Path]:
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
            try:
                lexical = Path(os.path.abspath(candidate))
                relative = lexical.relative_to(root)
            except (OSError, ValueError):
                continue
            if any(part in excluded_parts for part in relative.parts):
                if diagnostics is not None:
                    safe_candidate = contained_path(root, lexical, label="configured file")
                    if safe_candidate.is_file():
                        diagnostics.record_ignored(relative, "built_in_ignore")
                continue

            current = root
            for component in relative.parts:
                current /= component
                if current.is_symlink():
                    raise ConfigError(
                        f"configured file path must not contain symbolic links: {candidate}"
                    )
            try:
                lexical.resolve(strict=True).relative_to(root)
            except (OSError, ValueError):
                continue
            if lexical.is_file():
                # Preserve the lexical path so the no-follow reader can catch a symlink swap in
                # any parent component after discovery.
                found.add(lexical)
                if diagnostics is not None:
                    diagnostics.record_matched(relative)
                if len(found) > MAX_SCAN_FILES:
                    raise ConfigError(f"file discovery exceeds {MAX_SCAN_FILES} files")
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())
