"""Deterministic terminal, JSON, Markdown, and SARIF reports."""

from __future__ import annotations

import gzip
import json
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote

from repoinvariant import __version__
from repoinvariant.models import Finding, ScanResult, Severity

MAX_SARIF_RESULTS = 25_000
MAX_SARIF_COMPRESSED_BYTES = 10 * 1024 * 1024


def _relative(path: Path, root: Path) -> str:
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _summary(result: ScanResult) -> dict[str, int]:
    return {
        "files": len(result.scanned_files),
        "errors": result.error_count,
        "warnings": result.warning_count,
        "notes": sum(item.severity is Severity.NOTE for item in result.findings),
    }


def safe_console_text(value: str) -> str:
    """Keep terminal and Markdown reports on one inert physical line."""

    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        if (
            unicodedata.category(character).startswith("C")
            or character in {"\u2028", "\u2029"}
        ):
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    result = "".join(escaped)
    return f"./{result}" if result.startswith("::") else result


def _display(value: str) -> str:
    return safe_console_text(value)


def render_text(
    result: ScanResult, root: Path, fail_on: Severity = Severity.ERROR
) -> str:
    lines: list[str] = []
    for finding in result.sorted_findings():
        if finding.location:
            source = _display(_relative(finding.location.path, root))
            position = f"{source}:{finding.location.line}:{finding.location.column}"
        else:
            position = "."
        lines.append(
            f"{position}: {finding.severity.value} {finding.code}: {_display(finding.message)}"
        )
        if finding.hint:
            lines.append(f"  hint: {_display(finding.hint)}")
    summary = _summary(result)
    status = "FAIL" if result.blocks(fail_on) else "PASS"
    lines.append(
        f"{status}: {summary['files']} files, {summary['errors']} errors, "
        f"{summary['warnings']} warnings"
    )
    return "\n".join(lines) + "\n"


def _finding_payload(finding: Finding, root: Path) -> dict[str, Any]:
    payload = finding.as_dict()
    if finding.location:
        payload["location"]["path"] = _relative(finding.location.path, root)
    for index, related in enumerate(finding.related):
        payload["related"][index]["path"] = _relative(related.path, root)
    return payload


def render_json(
    result: ScanResult, root: Path, fail_on: Severity = Severity.ERROR
) -> str:
    blocking = result.blocks(fail_on)
    payload = {
        "schema_version": 1,
        "tool": {"name": "RepoInvariant", "version": __version__},
        "ok": not blocking,
        "exit_code": 1 if blocking else 0,
        "blocking_threshold": fail_on.value,
        "summary": _summary(result),
        "findings": [_finding_payload(item, root) for item in result.sorted_findings()],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _escape_table(value: str) -> str:
    replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\\": "&#92;",
        "|": "\\|",
        "`": "&#96;",
        "[": "&#91;",
        "]": "&#93;",
        "(": "&#40;",
        ")": "&#41;",
        "!": "&#33;",
        "*": "&#42;",
        "_": "&#95;",
    }
    return "".join(replacements.get(character, character) for character in _display(value))


def render_markdown(
    result: ScanResult, root: Path, fail_on: Severity = Severity.ERROR
) -> str:
    summary = _summary(result)
    status = "❌ Fail" if result.blocks(fail_on) else "✅ Pass"
    lines = [
        "# RepoInvariant report",
        "",
        f"**{status}** — {summary['files']} files, {summary['errors']} errors, "
        f"{summary['warnings']} warnings.",
        "",
    ]
    if result.findings:
        lines.extend(["| Severity | Code | Location | Message |", "|---|---|---|---|"])
        for item in result.sorted_findings():
            location = "."
            if item.location:
                location = f"{_relative(item.location.path, root)}:{item.location.line}"
            lines.append(
                f"| {item.severity.value} | {_escape_table(item.code)} | "
                f"{_escape_table(location)} | "
                f"{_escape_table(item.message)} |"
            )
    else:
        lines.append("No contract drift found.")
    return "\n".join(lines) + "\n"


def render_sarif(
    result: ScanResult, root: Path, fail_on: Severity = Severity.ERROR
) -> str:
    del fail_on  # SARIF encodes finding levels; the process exit policy is separate.
    findings = result.sorted_findings()
    if len(findings) > MAX_SARIF_RESULTS:
        raise ValueError(
            "SARIF report exceeds GitHub's limit of "
            f"{MAX_SARIF_RESULTS:,} results per run"
        )
    rules: dict[str, dict[str, Any]] = {}
    sarif_results: list[dict[str, Any]] = []
    for item in findings:
        rules.setdefault(
            item.code,
            {
                "id": item.code,
                "name": item.code,
                "shortDescription": {"text": f"RepoInvariant finding {item.code}"},
                "defaultConfiguration": {"level": _sarif_level(item.severity)},
            },
        )
        entry: dict[str, Any] = {
            "ruleId": item.code,
            "level": _sarif_level(item.severity),
            "message": {"text": item.message},
        }
        if item.location:
            entry["locations"] = [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": quote(_relative(item.location.path, root), safe="/@-._~")
                        },
                        "region": {
                            "startLine": max(item.location.line, 1),
                            "startColumn": max(item.location.column, 1),
                        },
                    }
                }
            ]
        sarif_results.append(entry)
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "RepoInvariant",
                        "version": __version__,
                        "informationUri": "https://github.com/xixvivji/RepoInvariant",
                        "rules": [rules[key] for key in sorted(rules)],
                    }
                },
                "results": sarif_results,
            }
        ],
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    compressed_size = len(gzip.compress(rendered.encode("utf-8"), mtime=0))
    if compressed_size > MAX_SARIF_COMPRESSED_BYTES:
        raise ValueError(
            "SARIF report exceeds GitHub's 10 MiB compressed upload limit "
            f"({compressed_size:,} bytes)"
        )
    return rendered


def _sarif_level(severity: Severity) -> str:
    return {
        Severity.ERROR: "error",
        Severity.WARNING: "warning",
        Severity.NOTE: "note",
    }[severity]


def render(
    result: ScanResult,
    root: Path,
    format_name: str,
    fail_on: Severity = Severity.ERROR,
) -> str:
    renderers = {
        "text": render_text,
        "json": render_json,
        "markdown": render_markdown,
        "sarif": render_sarif,
    }
    return renderers[format_name](result, root, fail_on)
