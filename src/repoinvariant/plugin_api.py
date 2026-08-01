"""Experimental, explicitly selected scanner plugin API.

Plugins are trusted in-process Python code.  This module constrains discovery, repository reads,
and accepted evidence, but it is not a process, filesystem, network, or time sandbox.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from repoinvariant.config import discover_files
from repoinvariant.filesystem import (
    MAX_SCAN_BYTES,
    MAX_SCAN_FILES,
    contained_path,
    read_limited_text,
)
from repoinvariant.import_safety import (
    UnsafeImportPathError,
    loaded_module_uses_roots,
    sanitize_import_path,
    sanitize_target_import_path,
)
from repoinvariant.models import Finding, Location, ScanResult, Severity

ENTRY_POINT_GROUP = "repoinvariant.scanners.v1"
PLUGIN_API_VERSION = 1
MAX_PLUGINS = 32
MAX_PLUGIN_RULES = 128
MAX_PLUGIN_FINDINGS = 10_000
MAX_PLUGIN_RELATED_LOCATIONS = 32
MAX_PLUGIN_TEXT = 2_048
MAX_PLUGIN_BASELINE_KEY = 512
MAX_PLUGIN_TOTAL_BYTES = 32 * 1024 * 1024

_PLUGIN_ID_RE = re.compile(
    r"^[a-z][a-z0-9]{0,31}(?:[._-][a-z0-9][a-z0-9]{0,31})*$",
    re.ASCII,
)
_RULE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,31}$", re.ASCII)
_BASELINE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,511}$", re.ASCII)
_DISTRIBUTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", re.ASCII)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}$", re.ASCII)
_MAX_LOCATION_VALUE = 10_000_000


class PluginError(ValueError):
    """Raised when plugin discovery, loading, execution, or evidence validation fails closed."""


@dataclass(frozen=True, slots=True)
class PluginRule:
    """One local rule declared by a scanner plugin."""

    code: str
    default_severity: Severity
    description: str


@dataclass(frozen=True, slots=True)
class PluginLocation:
    """A repository-relative one-based source location returned by a plugin."""

    path: str
    line: int = 1
    column: int = 1


@dataclass(frozen=True, slots=True)
class PluginFinding:
    """Immutable plugin evidence converted to a core ``Finding`` after validation."""

    rule: str
    message: str
    baseline_key: str
    location: PluginLocation | None = None
    hint: str | None = None
    related: tuple[PluginLocation, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginEntryPoint:
    """Data-only installed-entry-point metadata plus a deferred loader."""

    name: str
    distribution_name: str
    distribution_version: str
    loader: Callable[[], object] = field(repr=False, compare=False)
    target_module: str | None = None


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """A validated plugin and the exact metadata bound to a scan."""

    plugin_id: str
    distribution_name: str
    distribution_version: str
    rules: tuple[PluginRule, ...]
    config: Mapping[str, Any]
    rule_overrides: Mapping[str, str]
    scanner: object = field(repr=False, compare=False)


def _normalized_plugin_path(value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value:
        raise ValueError("plugin paths must be non-empty POSIX paths up to 512 characters")
    pure = PurePosixPath(value.removeprefix("./"))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or pure.as_posix() == ".":
        raise ValueError("plugin paths must stay inside the repository")
    return Path(*pure.parts)


class RepositoryView:
    """Bounded repository discovery and UTF-8 reads for one plugin invocation."""

    __slots__ = ("__cache", "__root", "__scanned_files", "__total_bytes")

    def __init__(self, root: Path) -> None:
        self.__root = Path(root).resolve(strict=True)
        self.__cache: dict[Path, str] = {}
        self.__scanned_files: set[Path] = set()
        self.__total_bytes = 0

    def _record(self, paths: Iterable[Path]) -> None:
        self.__scanned_files.update(paths)
        if len(self.__scanned_files) > MAX_SCAN_FILES:
            raise ValueError(f"plugin file discovery exceeds {MAX_SCAN_FILES} files")

    def files(self, patterns: Sequence[str]) -> tuple[str, ...]:
        """Return a stable tuple of repository-relative regular files matching safe globs."""

        if isinstance(patterns, (str, bytes)) or not isinstance(patterns, Sequence):
            raise ValueError("plugin file patterns must be a sequence")
        if not patterns or len(patterns) > 128:
            raise ValueError("plugins must provide between 1 and 128 file patterns")
        if not all(isinstance(item, str) and item.strip() for item in patterns):
            raise ValueError("plugin file patterns must be non-empty strings")
        discovered = discover_files(self.__root, tuple(patterns))
        relative = tuple(
            _normalized_plugin_path(path.relative_to(self.__root).as_posix())
            for path in discovered
        )
        self._record(relative)
        return tuple(path.as_posix() for path in relative)

    def read_text(self, path: str) -> str:
        """Read one repository-contained regular UTF-8 file within byte budgets."""

        relative = _normalized_plugin_path(path)
        cached = self.__cache.get(relative)
        if cached is not None:
            return cached
        candidate = contained_path(
            self.__root,
            self.__root / relative,
            label="plugin input file",
        )
        text = read_limited_text(candidate, root=self.__root, max_bytes=MAX_SCAN_BYTES)
        encoded_size = len(text.encode("utf-8"))
        if self.__total_bytes + encoded_size > MAX_PLUGIN_TOTAL_BYTES:
            raise ValueError(
                f"plugin reads exceed {MAX_PLUGIN_TOTAL_BYTES} UTF-8 bytes in one scan"
            )
        self.__total_bytes += encoded_size
        self.__cache[relative] = text
        self._record((relative,))
        return text

    @property
    def scanned_files(self) -> frozenset[Path]:
        """Return the content-free paths discovered or read by this invocation."""

        return frozenset(self.__scanned_files)


def _distribution_identity(distribution: metadata.Distribution) -> tuple[str, str]:
    name = distribution.metadata.get("Name", "")
    version = distribution.version
    if not isinstance(name, str) or not _DISTRIBUTION_RE.fullmatch(name):
        raise PluginError("an installed scanner plugin has invalid distribution metadata")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise PluginError("an installed scanner plugin has invalid distribution metadata")
    return name, version


def discover_plugin_entry_points(
    distributions: Iterable[metadata.Distribution] | None = None,
) -> tuple[PluginEntryPoint, ...]:
    """Discover metadata for the one supported group without importing plugin code."""

    try:
        installed = metadata.distributions() if distributions is None else distributions
        candidates: list[PluginEntryPoint] = []
        for distribution in installed:
            relevant = tuple(
                entry_point
                for entry_point in distribution.entry_points
                if entry_point.group == ENTRY_POINT_GROUP
            )
            if not relevant:
                continue
            name, version = _distribution_identity(distribution)
            candidates.extend(
                PluginEntryPoint(
                    name=entry_point.name,
                    distribution_name=name,
                    distribution_version=version,
                    loader=entry_point.load,
                    target_module=entry_point.module,
                )
                for entry_point in relevant
            )
    except PluginError:
        raise
    except BaseException:
        raise PluginError("installed scanner plugin metadata could not be read") from None
    return tuple(
        sorted(
            candidates,
            key=lambda item: (item.name, item.distribution_name, item.distribution_version),
        )
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in sorted(value.items())})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _copy_scope_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy_scope_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_copy_scope_value(item) for item in value]
    return value


def _plugin_settings(
    config: Mapping[str, Any], plugin_id: str
) -> tuple[Mapping[str, Any], Mapping[str, str]]:
    plugins = config.get("plugins", {})
    if not isinstance(plugins, Mapping):
        raise PluginError("plugin configuration is invalid")
    settings = plugins.get(plugin_id, {})
    if not isinstance(settings, Mapping):
        raise PluginError(f"plugin '{plugin_id}' configuration is invalid")
    plugin_config = settings.get("config", {})
    overrides = settings.get("rules", {})
    if not isinstance(plugin_config, Mapping) or not isinstance(overrides, Mapping):
        raise PluginError(f"plugin '{plugin_id}' configuration is invalid")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in overrides.items()):
        raise PluginError(f"plugin '{plugin_id}' rule configuration is invalid")
    return _freeze(plugin_config), MappingProxyType(dict(sorted(overrides.items())))


def _validated_rules(value: object, plugin_id: str) -> tuple[PluginRule, ...]:
    if not isinstance(value, tuple) or not value or len(value) > MAX_PLUGIN_RULES:
        raise PluginError(f"plugin '{plugin_id}' declares invalid rules")
    rules: list[PluginRule] = []
    seen: set[str] = set()
    for rule in value:
        if (
            not isinstance(rule, PluginRule)
            or not isinstance(rule.code, str)
            or not _RULE_RE.fullmatch(rule.code)
            or not isinstance(rule.default_severity, Severity)
            or not _bounded_text(rule.description, allow_empty=False)
            or rule.code in seen
        ):
            raise PluginError(f"plugin '{plugin_id}' declares invalid rules")
        seen.add(rule.code)
        rules.append(rule)
    return tuple(sorted(rules, key=lambda item: item.code))


def load_plugins(
    selected: Sequence[str],
    config: Mapping[str, Any],
    *,
    repository_root: Path | None = None,
    entry_points: Sequence[PluginEntryPoint] | None = None,
) -> tuple[LoadedPlugin, ...]:
    """Load only explicitly selected installed entry points, ordered by plugin ID."""

    if isinstance(selected, (str, bytes)) or not isinstance(selected, Sequence):
        raise PluginError("plugin selection must be a sequence of IDs")
    if len(selected) > MAX_PLUGINS:
        raise PluginError(f"no more than {MAX_PLUGINS} plugins may be selected")
    if any(
        not isinstance(item, str)
        or len(item) > 64
        or not _PLUGIN_ID_RE.fullmatch(item)
        for item in selected
    ):
        raise PluginError("plugin selection contains an invalid ID")
    if len(selected) != len(set(selected)):
        raise PluginError("plugin selection contains a duplicate ID")
    if not selected:
        return ()

    unsafe_roots: tuple[Path, ...] = ()
    if repository_root is None:
        if entry_points is None:
            raise PluginError("plugin loading requires a resolved repository root")
    else:
        try:
            resolved_root = Path(repository_root).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            raise PluginError("plugin loading requires a resolved repository root") from None
        if not resolved_root.is_dir():
            raise PluginError("plugin loading requires a resolved repository root")
        unsafe_roots = sanitize_import_path(repository_root=resolved_root)

    candidates = discover_plugin_entry_points() if entry_points is None else tuple(entry_points)
    by_name: dict[str, list[PluginEntryPoint]] = {}
    for candidate in candidates:
        by_name.setdefault(candidate.name, []).append(candidate)

    loaded: list[LoadedPlugin] = []
    for plugin_id in sorted(selected):
        matches = by_name.get(plugin_id, [])
        if not matches:
            raise PluginError(f"selected plugin '{plugin_id}' is not installed")
        if len(matches) != 1:
            raise PluginError(f"selected plugin '{plugin_id}' has duplicate entry points")
        candidate = matches[0]
        if (
            candidate.name != plugin_id
            or not _DISTRIBUTION_RE.fullmatch(candidate.distribution_name)
            or not _VERSION_RE.fullmatch(candidate.distribution_version)
            or (
                candidate.target_module is not None
                and (
                    not isinstance(candidate.target_module, str)
                    or not candidate.target_module
                )
            )
        ):
            raise PluginError(f"selected plugin '{plugin_id}' has invalid metadata")
        if candidate.target_module is not None and loaded_module_uses_roots(
            candidate.target_module, unsafe_roots
        ):
            raise PluginError(f"selected plugin '{plugin_id}' could not be loaded")
        if candidate.target_module is not None:
            try:
                sanitize_target_import_path(candidate.target_module, unsafe_roots)
            except UnsafeImportPathError:
                raise PluginError(
                    f"selected plugin '{plugin_id}' could not be loaded"
                ) from None
        try:
            scanner = candidate.loader()
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException:
            raise PluginError(f"selected plugin '{plugin_id}' could not be loaded") from None
        if candidate.target_module is not None and loaded_module_uses_roots(
            candidate.target_module, unsafe_roots
        ):
            raise PluginError(f"selected plugin '{plugin_id}' could not be loaded")
        try:
            api_version = scanner.api_version
            declared_id = scanner.plugin_id
            scan = scanner.scan
            rules = _validated_rules(scanner.rules, plugin_id)
        except PluginError:
            raise
        except BaseException:
            raise PluginError(f"selected plugin '{plugin_id}' is incompatible") from None
        if type(api_version) is not int or api_version != PLUGIN_API_VERSION:
            raise PluginError(f"selected plugin '{plugin_id}' uses an incompatible API version")
        if declared_id != plugin_id or not callable(scan):
            raise PluginError(f"selected plugin '{plugin_id}' is incompatible")
        try:
            plugin_config, overrides = _plugin_settings(config, plugin_id)
        except PluginError:
            raise
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException:
            raise PluginError(f"plugin '{plugin_id}' configuration is invalid") from None
        unknown_overrides = set(overrides) - {rule.code for rule in rules}
        if unknown_overrides or any(
            severity not in {"error", "warning", "off"} for severity in overrides.values()
        ):
            raise PluginError(f"plugin '{plugin_id}' rule configuration is invalid")
        loaded.append(
            LoadedPlugin(
                plugin_id=plugin_id,
                distribution_name=candidate.distribution_name,
                distribution_version=candidate.distribution_version,
                rules=rules,
                config=plugin_config,
                rule_overrides=overrides,
                scanner=scanner,
            )
        )
    return tuple(loaded)


def plugin_scope_payload(plugins: Sequence[LoadedPlugin]) -> list[dict[str, Any]]:
    """Return deterministic JSON-compatible metadata for adoption-baseline scope binding."""

    return [
        {
            "api_version": PLUGIN_API_VERSION,
            "config": _copy_scope_value(plugin.config),
            "distribution": {
                "name": plugin.distribution_name,
                "version": plugin.distribution_version,
            },
            "id": plugin.plugin_id,
            "rule_overrides": dict(plugin.rule_overrides),
            "rules": [
                {
                    "code": rule.code,
                    "default_severity": rule.default_severity.value,
                    "description": rule.description,
                }
                for rule in plugin.rules
            ],
        }
        for plugin in sorted(plugins, key=lambda item: item.plugin_id)
    ]


def _bounded_text(value: object, *, allow_empty: bool) -> bool:
    if not isinstance(value, str) or len(value) > MAX_PLUGIN_TEXT:
        return False
    if not allow_empty and not value:
        return False
    return not any(
        unicodedata.category(character).startswith("C") or character in {"\u2028", "\u2029"}
        for character in value
    )


def _validated_location(value: object, view: RepositoryView) -> Location:
    if (
        not isinstance(value, PluginLocation)
        or type(value.line) is not int
        or type(value.column) is not int
        or not 1 <= value.line <= _MAX_LOCATION_VALUE
        or not 1 <= value.column <= _MAX_LOCATION_VALUE
    ):
        raise PluginError("plugin returned an invalid source location")
    try:
        path = _normalized_plugin_path(value.path)
    except ValueError:
        raise PluginError("plugin returned an invalid source location") from None
    if path not in view.scanned_files:
        raise PluginError("plugin returned a location outside its bounded repository view")
    return Location(path, value.line, value.column)


def _validated_finding(
    value: object,
    plugin: LoadedPlugin,
    view: RepositoryView,
    rules: Mapping[str, PluginRule],
    identities: set[tuple[str, str]],
) -> Finding | None:
    if (
        not isinstance(value, PluginFinding)
        or not isinstance(value.rule, str)
        or value.rule not in rules
    ):
        raise PluginError(f"plugin '{plugin.plugin_id}' returned invalid evidence")
    if not _bounded_text(value.message, allow_empty=False):
        raise PluginError(f"plugin '{plugin.plugin_id}' returned invalid evidence")
    if value.hint is not None and not _bounded_text(value.hint, allow_empty=False):
        raise PluginError(f"plugin '{plugin.plugin_id}' returned invalid evidence")
    if not isinstance(value.baseline_key, str) or not _BASELINE_KEY_RE.fullmatch(
        value.baseline_key
    ):
        raise PluginError(f"plugin '{plugin.plugin_id}' returned invalid evidence")
    if not isinstance(value.related, tuple) or len(value.related) > MAX_PLUGIN_RELATED_LOCATIONS:
        raise PluginError(f"plugin '{plugin.plugin_id}' returned invalid evidence")
    location = _validated_location(value.location, view) if value.location is not None else None
    related = tuple(_validated_location(item, view) for item in value.related)
    identity = (f"{plugin.plugin_id}:{value.rule}", value.baseline_key)
    if identity in identities:
        raise PluginError(f"plugin '{plugin.plugin_id}' returned duplicate evidence")
    identities.add(identity)
    override = plugin.rule_overrides.get(value.rule)
    if override == "off":
        return None
    severity = Severity(override) if override is not None else rules[value.rule].default_severity
    baseline_key = f"plugin:{plugin.plugin_id}:{value.rule}:{value.baseline_key}"
    return Finding(
        code=f"{plugin.plugin_id}:{value.rule}",
        message=value.message,
        severity=severity,
        location=location,
        hint=value.hint,
        related=related,
        baseline_key=baseline_key,
    )


def scan_plugins(root: Path, plugins: Sequence[LoadedPlugin]) -> ScanResult:
    """Run validated plugins in deterministic order and accept no partial result on failure."""

    result = ScanResult()
    identities: set[tuple[str, str]] = set()
    raw_finding_count = 0
    for plugin in sorted(plugins, key=lambda item: item.plugin_id):
        view = RepositoryView(root)
        try:
            returned = plugin.scanner.scan(view, plugin.config)
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException:
            raise PluginError(f"plugin '{plugin.plugin_id}' failed during scanning") from None
        if not isinstance(returned, tuple) or len(returned) > MAX_PLUGIN_FINDINGS:
            raise PluginError(f"plugin '{plugin.plugin_id}' returned invalid evidence")
        raw_finding_count += len(returned)
        if raw_finding_count > MAX_PLUGIN_FINDINGS:
            raise PluginError(f"plugin findings exceed {MAX_PLUGIN_FINDINGS} in one scan")
        rules = {rule.code: rule for rule in plugin.rules}
        accepted: list[Finding] = []
        for value in returned:
            finding = _validated_finding(value, plugin, view, rules, identities)
            if finding is None:
                continue
            accepted.append(finding)
        result.findings.extend(accepted)
        result.scanned_files.update(view.scanned_files)
    result.findings = result.sorted_findings()
    return result


__all__ = [
    "ENTRY_POINT_GROUP",
    "PLUGIN_API_VERSION",
    "LoadedPlugin",
    "PluginEntryPoint",
    "PluginError",
    "PluginFinding",
    "PluginLocation",
    "PluginRule",
    "RepositoryView",
    "discover_plugin_entry_points",
    "load_plugins",
    "plugin_scope_payload",
    "scan_plugins",
]
