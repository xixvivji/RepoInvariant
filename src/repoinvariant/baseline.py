"""Deterministic, privacy-preserving baselines for gradual repository adoption."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repoinvariant import __version__
from repoinvariant.config import (
    DEFAULT_CONFIG,
    ConfigError,
    apply_optional_defaults,
    validate_config,
)
from repoinvariant.filesystem import MAX_SCAN_BYTES, read_limited_text
from repoinvariant.models import Finding, ScanResult, Severity

SCHEMA_VERSION = 1
FINGERPRINT_VERSION = 1
TOOL_NAME = "RepoInvariant"
MAX_BASELINE_BYTES = MAX_SCAN_BYTES
MAX_BASELINE_FINDINGS = 10_000

_SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{0,31}$", re.ASCII)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$", re.ASCII)


class BaselineError(ValueError):
    """Raised when a baseline cannot be created, loaded, or safely applied."""


@dataclass(frozen=True, slots=True)
class BaselineFinding:
    """The non-sensitive identity stored for one accepted finding."""

    code: str
    severity: Severity
    fingerprint: str


@dataclass(frozen=True, slots=True)
class Baseline:
    """A validated versioned baseline document."""

    schema_version: int
    fingerprint_version: int
    tool_name: str
    tool_version: str
    scope_digest: str
    findings: tuple[BaselineFinding, ...]


@dataclass(frozen=True, slots=True)
class BaselineApplication:
    """New-only scan output plus baseline maintenance statistics."""

    result: ScanResult
    suppressed_count: int
    stale_count: int


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not allowed")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise BaselineError(f"value cannot be represented as canonical JSON: {exc}") from exc


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _merge_defaults(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
    depth: int = 0,
    memo: dict[int, Any] | None = None,
) -> dict[str, Any]:
    if depth > 64:
        raise BaselineError("configuration nesting exceeds 64 levels")
    memo = memo if memo is not None else {}
    try:
        result = deepcopy(dict(base))
    except (TypeError, ValueError, RecursionError) as exc:
        raise BaselineError(f"configuration cannot be copied safely: {exc}") from exc
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            result[key] = _merge_defaults(existing, value, depth + 1, memo)
        else:
            try:
                result[key] = deepcopy(value, memo)
            except (TypeError, ValueError, RecursionError) as exc:
                raise BaselineError(f"configuration cannot be copied safely: {exc}") from exc
    return result


def _effective_config(config: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        raise BaselineError("configuration must be a mapping")
    try:
        effective = apply_optional_defaults(_merge_defaults(DEFAULT_CONFIG, config))
        validate_config(effective)
    except (ConfigError, RecursionError) as exc:
        raise BaselineError(f"invalid effective configuration: {exc}") from exc
    return effective


def compute_scope_digest(
    config: Mapping[str, Any],
    *,
    no_env: bool = False,
    no_features: bool = False,
    no_versions: bool = False,
) -> str:
    """Hash the effective scan configuration and enabled scanner set."""

    if any(type(flag) is not bool for flag in (no_env, no_features, no_versions)):
        raise BaselineError("scanner selection flags must be booleans")
    effective = _effective_config(config)
    enabled_scanners = [
        name
        for name, disabled in (("env", no_env), ("features", no_features))
        if not disabled
    ]
    versions = effective.get("versions")
    if isinstance(versions, Mapping) and "java" in versions and not no_versions:
        enabled_scanners.append("versions")
    payload = {
        "config": effective,
        "enabled_scanners": enabled_scanners,
        "fingerprint_version": FINGERPRINT_VERSION,
    }
    return _sha256(_canonical_json(payload))


def finding_fingerprint(finding: Finding) -> str | None:
    """Return the stable v1 identity for a finding, or ``None`` when it is unmatchable."""

    baseline_key = getattr(finding, "baseline_key", None)
    if baseline_key is None:
        return None
    if not isinstance(baseline_key, str) or not baseline_key:
        return None
    if not isinstance(finding.code, str) or not _CODE_RE.fullmatch(finding.code):
        raise BaselineError("finding code is not a safe baseline identifier")
    payload = ["repoinvariant-finding", FINGERPRINT_VERSION, finding.code, baseline_key]
    return _sha256(_canonical_json(payload))


def _validate_baseline(baseline: Baseline) -> None:
    if type(baseline.schema_version) is not int or baseline.schema_version != SCHEMA_VERSION:
        raise BaselineError(f"only baseline schema version {SCHEMA_VERSION} is supported")
    if (
        type(baseline.fingerprint_version) is not int
        or baseline.fingerprint_version != FINGERPRINT_VERSION
    ):
        raise BaselineError(
            f"only finding fingerprint version {FINGERPRINT_VERSION} is supported"
        )
    if baseline.tool_name != TOOL_NAME:
        raise BaselineError(f"baseline tool name must be {TOOL_NAME!r}")
    if not isinstance(baseline.tool_version, str) or not _VERSION_RE.fullmatch(
        baseline.tool_version
    ):
        raise BaselineError("baseline tool version is invalid")
    if not isinstance(baseline.scope_digest, str) or not _SHA256_DIGEST_RE.fullmatch(
        baseline.scope_digest
    ):
        raise BaselineError("baseline scope digest must be a lowercase SHA-256 digest")
    if not isinstance(baseline.findings, tuple):
        raise BaselineError("baseline findings must be an immutable sequence")
    if len(baseline.findings) > MAX_BASELINE_FINDINGS:
        raise BaselineError(f"baseline exceeds {MAX_BASELINE_FINDINGS} findings")

    fingerprints: set[str] = set()
    for finding in baseline.findings:
        if not isinstance(finding, BaselineFinding):
            raise BaselineError("baseline findings contain an invalid entry")
        if not isinstance(finding.code, str) or not _CODE_RE.fullmatch(finding.code):
            raise BaselineError("baseline finding code is invalid")
        if not isinstance(finding.severity, Severity):
            raise BaselineError("baseline finding severity is invalid")
        if not isinstance(finding.fingerprint, str) or not _SHA256_DIGEST_RE.fullmatch(
            finding.fingerprint
        ):
            raise BaselineError("baseline finding fingerprint must be a lowercase SHA-256 digest")
        if finding.fingerprint in fingerprints:
            raise BaselineError(f"duplicate baseline fingerprint: {finding.fingerprint}")
        fingerprints.add(finding.fingerprint)
    if list(baseline.findings) != sorted(
        baseline.findings,
        key=lambda item: (item.code, item.fingerprint),
    ):
        raise BaselineError("baseline findings must be sorted by code and fingerprint")


def create_baseline(
    result: ScanResult,
    config: Mapping[str, Any],
    *,
    no_env: bool = False,
    no_features: bool = False,
    no_versions: bool = False,
    tool_version: str = __version__,
) -> Baseline:
    """Create a baseline from matchable findings without retaining finding payloads."""

    entries: list[BaselineFinding] = []
    for finding in result.sorted_findings():
        fingerprint = finding_fingerprint(finding)
        if fingerprint is None:
            raise BaselineError(
                f"finding {finding.code!r} has no stable baseline key; refusing to omit it"
            )
        entries.append(
            BaselineFinding(
                code=finding.code,
                severity=finding.severity,
                fingerprint=fingerprint,
            )
        )
    entries.sort(key=lambda item: (item.code, item.fingerprint))
    baseline = Baseline(
        schema_version=SCHEMA_VERSION,
        fingerprint_version=FINGERPRINT_VERSION,
        tool_name=TOOL_NAME,
        tool_version=tool_version,
        scope_digest=compute_scope_digest(
            config,
            no_env=no_env,
            no_features=no_features,
            no_versions=no_versions,
        ),
        findings=tuple(entries),
    )
    _validate_baseline(baseline)
    return baseline


def render_baseline(baseline: Baseline) -> str:
    """Serialize a baseline as stable, human-reviewable JSON."""

    _validate_baseline(baseline)
    payload = {
        "schema_version": baseline.schema_version,
        "fingerprint_version": baseline.fingerprint_version,
        "tool": {"name": baseline.tool_name, "version": baseline.tool_version},
        "scope_digest": baseline.scope_digest,
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity.value,
                "fingerprint": finding.fingerprint,
            }
            for finding in baseline.findings
        ],
    }
    try:
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
    except (TypeError, ValueError, RecursionError) as exc:
        raise BaselineError(f"baseline cannot be serialized: {exc}") from exc


def _require_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise BaselineError(f"{label} is missing key(s): {', '.join(missing)}")
    if unknown:
        raise BaselineError(f"{label} has unknown key(s): {', '.join(map(repr, unknown))}")


def _parse_baseline(value: Any) -> Baseline:
    if not isinstance(value, Mapping):
        raise BaselineError("baseline document must be a JSON object")
    _require_keys(
        value,
        frozenset(
            {"schema_version", "fingerprint_version", "tool", "scope_digest", "findings"}
        ),
        "baseline document",
    )
    if type(value["schema_version"]) is not int:
        raise BaselineError("baseline schema_version must be an integer")
    if type(value["fingerprint_version"]) is not int:
        raise BaselineError("baseline fingerprint_version must be an integer")

    tool = value["tool"]
    if not isinstance(tool, Mapping):
        raise BaselineError("baseline tool must be a JSON object")
    _require_keys(tool, frozenset({"name", "version"}), "baseline tool")
    if not isinstance(tool["name"], str) or not isinstance(tool["version"], str):
        raise BaselineError("baseline tool name and version must be strings")

    raw_findings = value["findings"]
    if not isinstance(raw_findings, list):
        raise BaselineError("baseline findings must be a JSON array")
    if len(raw_findings) > MAX_BASELINE_FINDINGS:
        raise BaselineError(f"baseline exceeds {MAX_BASELINE_FINDINGS} findings")
    findings: list[BaselineFinding] = []
    for index, raw in enumerate(raw_findings):
        if not isinstance(raw, Mapping):
            raise BaselineError(f"baseline findings[{index}] must be a JSON object")
        _require_keys(raw, frozenset({"code", "severity", "fingerprint"}), f"findings[{index}]")
        if not all(isinstance(raw[key], str) for key in ("code", "severity", "fingerprint")):
            raise BaselineError(f"baseline findings[{index}] values must be strings")
        try:
            severity = Severity(raw["severity"])
        except ValueError as exc:
            raise BaselineError(
                f"baseline findings[{index}] has unknown severity {raw['severity']!r}"
            ) from exc
        findings.append(
            BaselineFinding(
                code=raw["code"],
                severity=severity,
                fingerprint=raw["fingerprint"],
            )
        )

    if not isinstance(value["scope_digest"], str):
        raise BaselineError("baseline scope_digest must be a string")
    baseline = Baseline(
        schema_version=value["schema_version"],
        fingerprint_version=value["fingerprint_version"],
        tool_name=tool["name"],
        tool_version=tool["version"],
        scope_digest=value["scope_digest"],
        findings=tuple(findings),
    )
    _validate_baseline(baseline)
    return baseline


def load_baseline(root: Path, path: Path) -> Baseline:
    """Load a bounded UTF-8 baseline without following repository symlinks."""

    try:
        resolved_root = root.resolve()
        candidate = path if path.is_absolute() else resolved_root / path
        text = read_limited_text(
            candidate,
            root=resolved_root,
            max_bytes=MAX_BASELINE_BYTES,
            errors="strict",
        )
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        return _parse_baseline(value)
    except BaselineError:
        raise
    except (
        OSError,
        UnicodeError,
        ValueError,
        RuntimeError,
        RecursionError,
        json.JSONDecodeError,
    ) as exc:
        raise BaselineError(f"cannot read baseline {path}: {exc}") from exc


def apply_baseline(
    result: ScanResult,
    baseline: Baseline,
    config: Mapping[str, Any],
    *,
    no_env: bool = False,
    no_features: bool = False,
    no_versions: bool = False,
) -> BaselineApplication:
    """Suppress matching accepted findings while leaving the source result unchanged."""

    _validate_baseline(baseline)
    expected_scope = compute_scope_digest(
        config,
        no_env=no_env,
        no_features=no_features,
        no_versions=no_versions,
    )
    if not hmac.compare_digest(baseline.scope_digest, expected_scope):
        raise BaselineError(
            "baseline scope does not match the effective configuration and enabled scanners"
        )

    accepted = {finding.fingerprint: finding for finding in baseline.findings}
    matched: set[str] = set()
    current_fingerprints: set[str] = set()
    new_findings: list[Finding] = []
    suppressed_count = 0
    for finding in result.findings:
        fingerprint = finding_fingerprint(finding)
        if fingerprint is not None:
            if fingerprint in current_fingerprints:
                raise BaselineError(f"duplicate current finding fingerprint: {fingerprint}")
            current_fingerprints.add(fingerprint)
        entry = accepted.get(fingerprint) if fingerprint is not None else None
        if (
            entry is not None
            and entry.code == finding.code
            and entry.severity is finding.severity
        ):
            matched.add(entry.fingerprint)
            suppressed_count += 1
        else:
            new_findings.append(finding)

    filtered = ScanResult(
        findings=new_findings,
        scanned_files=set(result.scanned_files),
    )
    return BaselineApplication(
        result=filtered,
        suppressed_count=suppressed_count,
        stale_count=len(accepted.keys() - matched),
    )


__all__ = [
    "Baseline",
    "BaselineApplication",
    "BaselineError",
    "BaselineFinding",
    "FINGERPRINT_VERSION",
    "MAX_BASELINE_BYTES",
    "MAX_BASELINE_FINDINGS",
    "SCHEMA_VERSION",
    "apply_baseline",
    "compute_scope_digest",
    "create_baseline",
    "finding_fingerprint",
    "load_baseline",
    "render_baseline",
]
