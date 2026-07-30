"""Installation diagnostics for configured RepoInvariant scan ranges."""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repoinvariant import __version__
from repoinvariant.baseline import compute_scope_digest, load_baseline
from repoinvariant.config import CONFIG_NAME, VERSION_RULE_DEFAULTS
from repoinvariant.diagnostics import DiagnosticBudget, ScannerDiagnostics, SourceDiagnostics
from repoinvariant.env_contracts import scan_env_contracts
from repoinvariant.filesystem import MAX_SCAN_FILES
from repoinvariant.models import ScanResult
from repoinvariant.reporters import safe_console_text
from repoinvariant.traceability import scan_traceability
from repoinvariant.version_contracts import scan_version_contracts

SCHEMA_VERSION = 1
PATHS_PER_COLLECTION = 50
MAX_VERBOSE_ITEMS = 1_000
MAX_VERBOSE_BYTES = 256 * 1024
MAX_REPORT_BYTES = 1024 * 1024

_SCANNERS = ("env", "features", "versions")
_SOURCES = {
    "env": ("contracts", "compose", "kubernetes", "workflows", "spring"),
    "features": ("requirements", "specifications", "tests"),
    "versions": ("gradle", "dockerfiles", "compose", "workflows", "docs"),
}
_RULES = {
    "ENV001": ("env", "error"),
    "ENV002": ("env", "warning"),
    "ENV003": ("env", "warning"),
    "TRACE001": ("features", "error"),
    "TRACE002": ("features", "error"),
    "TRACE003": ("features", "error"),
    "TRACE004": ("features", "warning"),
    **{code: ("versions", severity) for code, severity in VERSION_RULE_DEFAULTS.items()},
}


@dataclass(slots=True)
class _VerboseBudget:
    enabled: bool
    items: int = 0
    encoded_bytes: int = 0
    truncated: bool = False

    def take(
        self,
        values: Iterable[str],
        *,
        limit: int | None = None,
        deduplicate: bool = True,
    ) -> tuple[list[str], int]:
        materialized = list(values)
        ordered = sorted(set(materialized) if deduplicate else materialized)
        if not self.enabled:
            return [], len(ordered)

        selected: list[str] = []
        collection_limit = PATHS_PER_COLLECTION if limit is None else limit
        for value in ordered:
            encoded_size = max(
                len(json.dumps(value, ensure_ascii=True).encode("ascii")),
                len(safe_console_text(value).encode("utf-8")),
            )
            if (
                len(selected) >= collection_limit
                or self.items >= MAX_VERBOSE_ITEMS
                or self.encoded_bytes + encoded_size > MAX_VERBOSE_BYTES
            ):
                break
            selected.append(value)
            self.items += 1
            self.encoded_bytes += encoded_size
        omitted = len(ordered) - len(selected)
        if omitted:
            self.truncated = True
        return selected, omitted


def _as_patterns(value: object) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _scanner_state(*, configured: bool, disabled: bool) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not configured:
        reasons.append("scanner_not_configured")
    if disabled:
        reasons.append("disabled_by_flag")
    if disabled:
        return "disabled_by_flag", reasons
    if not configured:
        return "not_configured", reasons
    return "active", reasons


def _configured_diagnostics(
    config: Mapping[str, Any], scanner: str, budget: DiagnosticBudget
) -> ScannerDiagnostics:
    diagnostics = ScannerDiagnostics(scanner, budget=budget)
    if scanner in {"env", "features"}:
        section = config.get(scanner)
        mapping = section if isinstance(section, Mapping) else {}
        for source in _SOURCES[scanner]:
            diagnostics.source(source, _as_patterns(mapping.get(source)))
        return diagnostics

    versions = config.get("versions")
    java = versions.get("java") if isinstance(versions, Mapping) else None
    mapping = java if isinstance(java, Mapping) else {}
    required = set(_as_patterns(mapping.get("required")))
    for source in _SOURCES[scanner]:
        diagnostics.source(
            source,
            _as_patterns(mapping.get(source)),
            required=source in required,
        )
    return diagnostics


def _path_collection(paths: Iterable[Path], budget: _VerboseBudget) -> dict[str, Any]:
    values = (path.as_posix() for path in paths)
    selected, omitted = budget.take(values)
    return {
        "count": len(selected) + omitted,
        "paths": selected,
        "omitted_count": omitted,
    }


def _pattern_collection(patterns: Iterable[str], budget: _VerboseBudget) -> dict[str, Any]:
    selected, omitted = budget.take(patterns, deduplicate=False)
    return {
        "count": len(selected) + omitted,
        "values": selected,
        "omitted_count": omitted,
    }


def _ignored_collection(
    ignored: Mapping[Path, str], budget: _VerboseBudget
) -> dict[str, Any]:
    ordered = sorted((path.as_posix(), reason) for path, reason in ignored.items())
    selected_paths, omitted = budget.take(path for path, _ in ordered)
    reasons = dict(ordered)
    return {
        "count": len(ordered),
        "records": [
            {"path": path, "reason": reasons[path]} for path in selected_paths
        ],
        "omitted_count": omitted,
    }


def _source_state(source: SourceDiagnostics, scanner_state: str) -> str:
    if scanner_state == "not_configured":
        return "not_configured"
    if scanner_state == "disabled_by_flag":
        return "disabled"
    if not source.patterns:
        return "empty_patterns"
    if source.matched_files or source.derived_files:
        return "matched"
    if source.ignored_files:
        return "all_ignored"
    return "no_matches"


def _source_payload(
    source: SourceDiagnostics,
    scanner_state: str,
    budget: _VerboseBudget,
) -> dict[str, Any]:
    return {
        "name": source.name,
        "state": _source_state(source, scanner_state),
        "required": source.required,
        "patterns": _pattern_collection(source.patterns, budget),
        "matched": _path_collection(source.matched_files, budget),
        "derived": _path_collection(source.derived_files, budget),
        "ignored": _ignored_collection(source.ignored_files, budget),
    }


def _repository_argument_path(root: Path, path: Path) -> str:
    candidate = path if path.is_absolute() else root / path
    lexical = Path(os.path.abspath(candidate))
    try:
        return lexical.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("diagnostic input path must stay inside repository root") from exc


def _configuration_payload(root: Path, config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        default_path = root / CONFIG_NAME
        if not default_path.exists() and not default_path.is_symlink():
            return {"source": "defaults", "path": None}
        return {"source": "file", "path": CONFIG_NAME}
    return {"source": "file", "path": _repository_argument_path(root, config_path)}


def _baseline_payload(
    root: Path,
    config: Mapping[str, Any],
    baseline_path: Path | None,
    *,
    no_env: bool,
    no_features: bool,
    no_versions: bool,
) -> dict[str, Any]:
    if baseline_path is None:
        return {"status": "not_selected", "path": None, "scope_match": None}
    baseline = load_baseline(root, baseline_path)
    scope_match = hmac.compare_digest(
        baseline.scope_digest,
        compute_scope_digest(
            config,
            no_env=no_env,
            no_features=no_features,
            no_versions=no_versions,
        ),
    )
    return {
        "status": "match" if scope_match else "mismatch",
        "path": _repository_argument_path(root, baseline_path),
        "scope_match": scope_match,
    }


def _scanner_payload(
    name: str,
    state: str,
    reasons: list[str],
    diagnostics: ScannerDiagnostics,
    result: ScanResult,
    budget: _VerboseBudget,
) -> dict[str, Any]:
    files = _path_collection(result.scanned_files, budget)
    sources = [
        _source_payload(diagnostics.sources[source], state, budget)
        for source in _SOURCES[name]
    ]
    return {
        "name": name,
        "state": state,
        "configured": "scanner_not_configured" not in reasons,
        "enabled": state == "active",
        "inactive_reasons": reasons,
        "empty": state == "active" and not result.scanned_files,
        "files": files,
        "sources": sources,
    }


def _rule_payloads(
    config: Mapping[str, Any], scanner_states: Mapping[str, str]
) -> list[dict[str, Any]]:
    configured = config.get("rules")
    rules = configured if isinstance(configured, Mapping) else {}
    payloads: list[dict[str, Any]] = []
    for code, (scanner, default_severity) in sorted(_RULES.items()):
        severity = rules.get(code, default_severity)
        reasons: list[str] = []
        if severity == "off":
            reasons.append("rule_off")
        if scanner_states[scanner] == "disabled_by_flag":
            reasons.append("scanner_disabled")
        elif scanner_states[scanner] == "not_configured":
            reasons.append("scanner_not_configured")
        payloads.append(
            {
                "code": code,
                "scanner": scanner,
                "severity": severity,
                "active": not reasons,
                "inactive_reasons": reasons,
            }
        )
    return payloads


def build_doctor_report(
    root: Path,
    config: Mapping[str, Any],
    *,
    config_path: Path | None = None,
    baseline_path: Path | None = None,
    no_env: bool = False,
    no_features: bool = False,
    no_versions: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run configured scanners once and return a deterministic diagnostic model."""

    if any(type(flag) is not bool for flag in (no_env, no_features, no_versions, verbose)):
        raise ValueError("doctor selection flags must be booleans")
    root = root.resolve(strict=True)
    versions = config.get("versions")
    version_configured = isinstance(versions, Mapping) and isinstance(
        versions.get("java"), Mapping
    )
    configured = {"env": True, "features": True, "versions": version_configured}
    disabled = {
        "env": no_env,
        "features": no_features,
        "versions": no_versions,
    }
    state_details = {
        name: _scanner_state(configured=configured[name], disabled=disabled[name])
        for name in _SCANNERS
    }
    diagnostic_budget = DiagnosticBudget()
    diagnostics = {
        name: _configured_diagnostics(config, name, diagnostic_budget) for name in _SCANNERS
    }
    results = {name: ScanResult() for name in _SCANNERS}
    scan_functions = {
        "env": scan_env_contracts,
        "features": scan_traceability,
        "versions": scan_version_contracts,
    }
    unique_files: set[Path] = set()
    for name in _SCANNERS:
        state, _ = state_details[name]
        if state != "active":
            continue
        results[name] = scan_functions[name](root, config, diagnostics=diagnostics[name])
        # Doctor reports scan coverage, never finding evidence or discovered identifiers.
        results[name].findings.clear()
        unique_files.update(results[name].scanned_files)
        if len(unique_files) > MAX_SCAN_FILES:
            raise ValueError(f"doctor target discovery exceeds {MAX_SCAN_FILES} unique files")

    budget = _VerboseBudget(verbose)
    scanners = [
        _scanner_payload(
            name,
            state_details[name][0],
            state_details[name][1],
            diagnostics[name],
            results[name],
            budget,
        )
        for name in _SCANNERS
    ]
    scanner_states = {item["name"]: item["state"] for item in scanners}
    rules = _rule_payloads(config, scanner_states)
    source_states = [source["state"] for scanner in scanners for source in scanner["sources"]]
    inactive_scanners = sum(item["state"] != "active" for item in scanners)
    inactive_rules = sum(not item["active"] for item in rules)
    ignored_files = sum(
        source["ignored"]["count"] for scanner in scanners for source in scanner["sources"]
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "RepoInvariant", "version": __version__},
        "repository": ".",
        "configuration": _configuration_payload(root, config_path),
        "baseline": _baseline_payload(
            root,
            config,
            baseline_path,
            no_env=no_env,
            no_features=no_features,
            no_versions=no_versions,
        ),
        "verbose": {
            "enabled": verbose,
            "path_limit": PATHS_PER_COLLECTION,
            "item_limit": MAX_VERBOSE_ITEMS,
            "truncated": budget.truncated,
        },
        "scanners": scanners,
        "rules": rules,
        "summary": {
            "active_scanners": len(scanners) - inactive_scanners,
            "inactive_scanners": inactive_scanners,
            "unique_scanned_files": len(unique_files),
            "empty_source_ranges": sum(
                state in {"empty_patterns", "no_matches", "all_ignored"}
                for state in source_states
            ),
            "ignored_file_records": ignored_files,
            "active_rules": len(rules) - inactive_rules,
            "inactive_rules": inactive_rules,
        },
    }
    return report


def _format_path_list(lines: list[str], label: str, values: list[str], omitted: int) -> None:
    if not values and not omitted:
        return
    lines.append(f"    {label}:")
    for value in values:
        lines.append(f"      - {safe_console_text(value)}")
    if omitted:
        lines.append(f"      ... {omitted} not listed")


def _render_text(report: Mapping[str, Any]) -> str:
    configuration = report["configuration"]
    baseline = report["baseline"]
    summary = report["summary"]
    lines = ["RepoInvariant doctor"]
    config_label = configuration["path"] or "built-in defaults"
    lines.append(f"Configuration: {safe_console_text(str(config_label))}")
    baseline_label = baseline["status"].replace("_", " ")
    if baseline["path"]:
        baseline_label += f" ({safe_console_text(str(baseline['path']))})"
    lines.append(f"Baseline: {baseline_label}")
    lines.append(
        "Scanners: "
        f"{summary['active_scanners']} active, {summary['inactive_scanners']} inactive; "
        f"{summary['unique_scanned_files']} unique scanned file(s)"
    )
    verbose = bool(report["verbose"]["enabled"])
    for scanner in report["scanners"]:
        lines.append(
            f"- {scanner['name']}: {scanner['state']} "
            f"({scanner['files']['count']} scanned file(s))"
        )
        if verbose:
            _format_path_list(
                lines,
                "actual targets",
                scanner["files"]["paths"],
                scanner["files"]["omitted_count"],
            )
        for source in scanner["sources"]:
            required_label = "; required" if source["required"] else ""
            lines.append(
                f"  - {source['name']}: {source['state']}{required_label}; "
                f"{source['matched']['count']} matched, "
                f"{source['derived']['count']} derived, "
                f"{source['ignored']['count']} ignored, "
                f"{source['patterns']['count']} pattern(s)"
            )
            if not verbose:
                continue
            _format_path_list(
                lines,
                "patterns",
                source["patterns"]["values"],
                source["patterns"]["omitted_count"],
            )
            _format_path_list(
                lines,
                "matched paths",
                source["matched"]["paths"],
                source["matched"]["omitted_count"],
            )
            _format_path_list(
                lines,
                "derived paths",
                source["derived"]["paths"],
                source["derived"]["omitted_count"],
            )
            records = source["ignored"]["records"]
            if records or source["ignored"]["omitted_count"]:
                lines.append("    ignored paths:")
                for record in records:
                    lines.append(
                        "      - "
                        f"{safe_console_text(record['path'])} "
                        f"({record['reason']})"
                    )
                if source["ignored"]["omitted_count"]:
                    lines.append(
                        f"      ... {source['ignored']['omitted_count']} not listed"
                    )
    lines.append(
        f"Rules: {summary['active_rules']} active, {summary['inactive_rules']} inactive"
    )
    for rule in report["rules"]:
        if rule["active"]:
            lines.append(f"- {rule['code']}: {rule['severity']} (active)")
            continue
        reasons = ", ".join(rule["inactive_reasons"])
        lines.append(
            f"- {rule['code']}: {rule['severity']} (inactive: {reasons})"
        )
    lines.append(
        "Diagnosis complete: "
        f"{summary['empty_source_ranges']} empty source range(s), "
        f"{summary['ignored_file_records']} ignored file record(s)."
    )
    return "\n".join(lines) + "\n"


def render_doctor_report(report: Mapping[str, Any], format_name: str) -> str:
    """Render a doctor model without exposing scanner findings or file contents."""

    if format_name == "json":
        rendered = json.dumps(
            report,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ) + "\n"
    elif format_name == "text":
        rendered = _render_text(report)
    else:
        raise ValueError(f"unsupported doctor report format: {format_name}")
    if len(rendered.encode("utf-8", errors="surrogatepass")) > MAX_REPORT_BYTES:
        raise ValueError(f"doctor report exceeds {MAX_REPORT_BYTES} bytes")
    return rendered


__all__ = [
    "MAX_VERBOSE_ITEMS",
    "PATHS_PER_COLLECTION",
    "build_doctor_report",
    "render_doctor_report",
]
