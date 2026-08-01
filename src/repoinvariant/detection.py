"""Deterministic, convention-based starter configuration detection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from repoinvariant.config import (
    DEFAULT_CONFIG,
    VERSION_JAVA_DEFAULTS,
    VERSION_RULE_DEFAULTS,
    ConfigError,
    discover_files,
)
from repoinvariant.filesystem import MAX_SCAN_FILES, read_limited_text
from repoinvariant.version_contracts import parse_java_version_file_major

_SCHEMA_URL = (
    "https://raw.githubusercontent.com/xixvivji/RepoInvariant/"
    "main/schemas/repoinvariant-config-v1.schema.json"
)
_ENV_SOURCES = ("contracts", "compose", "kubernetes", "workflows", "spring")
_FEATURE_SOURCES = ("requirements", "specifications", "tests")
_VERSION_SOURCES = tuple(
    key for key in VERSION_JAVA_DEFAULTS if key not in {"ignore", "required"}
)
def _patterns(section: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = section.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _detected_patterns(
    root: Path,
    patterns: Sequence[str],
    discovered: set[Path],
) -> list[str]:
    selected: list[str] = []
    for pattern in patterns:
        matches = discover_files(root, [pattern])
        if not matches:
            continue
        selected.append(pattern)
        discovered.update(path.relative_to(root) for path in matches)
        if len(discovered) > MAX_SCAN_FILES:
            raise ConfigError(
                f"configuration detection exceeds {MAX_SCAN_FILES} unique files"
            )
    return selected


def _canonical_java_major(root: Path) -> str | None:
    version_file = root / ".java-version"
    if not version_file.exists() and not version_file.is_symlink():
        return None
    try:
        value = read_limited_text(version_file, root=root, max_bytes=512).strip()
    except (OSError, UnicodeError, ValueError) as exc:
        raise ConfigError(f"cannot inspect {version_file}: {exc}") from exc
    if not value or len(value) > 128:
        return None
    return parse_java_version_file_major(value)


def detect_config(root: Path) -> dict[str, Any]:
    """Build the smallest valid config covering supported artifacts already present."""

    root = root.resolve(strict=True)
    discovered: set[Path] = set()
    default_env = DEFAULT_CONFIG["env"]
    default_features = DEFAULT_CONFIG["features"]
    if not isinstance(default_env, Mapping) or not isinstance(default_features, Mapping):
        raise AssertionError("built-in configuration sections must be mappings")

    env: dict[str, Any] = {
        source: _detected_patterns(
            root,
            _patterns(default_env, source),
            discovered,
        )
        for source in _ENV_SOURCES
    }
    env["ignore"] = list(_patterns(default_env, "ignore"))

    features: dict[str, Any] = {
        source: _detected_patterns(
            root,
            _patterns(default_features, source),
            discovered,
        )
        for source in _FEATURE_SOURCES
    }
    features.update(
        {
            "id_pattern": default_features["id_pattern"],
            "openapi_extension": default_features["openapi_extension"],
            "requirements_mode": default_features["requirements_mode"],
            "ignore": list(_patterns(default_features, "ignore")),
        }
    )

    config: dict[str, Any] = {
        "version": 1,
        "env": env,
        "features": features,
    }
    java_major = _canonical_java_major(root)
    if java_major is not None:
        java_sources = {
            source: _detected_patterns(
                root,
                _patterns(VERSION_JAVA_DEFAULTS, source),
                discovered,
            )
            for source in _VERSION_SOURCES
        }
        if any(java_sources.values()):
            config["versions"] = {
                "java": {
                    "expected": java_major,
                    **java_sources,
                    "ignore": [],
                    "required": [],
                }
            }

    env_detected = any(env[source] for source in _ENV_SOURCES)
    features_detected = any(features[source] for source in _FEATURE_SOURCES)
    versions_detected = "versions" in config
    if not (env_detected or features_detected or versions_detected):
        raise ConfigError("no supported repository artifacts were detected")

    default_rules = DEFAULT_CONFIG["rules"]
    if not isinstance(default_rules, Mapping):
        raise AssertionError("built-in rule configuration must be a mapping")
    rules: dict[str, str] = {}
    for code, severity in default_rules.items():
        if not isinstance(code, str) or not isinstance(severity, str):
            raise AssertionError("built-in rule policies must be strings")
        enabled = env_detected if code.startswith("ENV") else features_detected
        rules[code] = severity if enabled else "off"
    if versions_detected:
        rules.update(VERSION_RULE_DEFAULTS)
    config["rules"] = rules
    return config


def render_detected_config(config: Mapping[str, Any]) -> str:
    """Render a stable config without timestamps or machine-specific paths."""

    document = yaml.safe_dump(
        dict(config),
        allow_unicode=False,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    )
    return (
        f"# yaml-language-server: $schema={_SCHEMA_URL}\n"
        "# Generated by `repoinvariant init --detect`; review before committing.\n"
        + document
    )


__all__ = ["detect_config", "render_detected_config"]
