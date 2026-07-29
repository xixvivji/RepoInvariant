"""Deterministic terminal, JSON, Markdown, and SARIF reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from repotruth import __version__
from repotruth.models import Finding, ScanResult, Severity


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


def render_text(result: ScanResult, root: Path) -> str:
    lines: list[str] = []
    for finding in result.sorted_findings():
        if finding.location:
            source = _relative(finding.location.path, root)
            position = f"{source}:{finding.location.line}:{finding.location.column}"
        else:
            position = "."
        lines.append(f"{position}: {finding.severity.value} {finding.code}: {finding.message}")
        if finding.hint:
            lines.append(f"  hint: {finding.hint}")
    summary = _summary(result)
    status = "PASS" if result.ok else "FAIL"
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


def render_json(result: ScanResult, root: Path) -> str:
    payload = {
        "tool": {"name": "RepoTruth", "version": __version__},
        "ok": result.ok,
        "summary": _summary(result),
        "findings": [_finding_payload(item, root) for item in result.sorted_findings()],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(result: ScanResult, root: Path) -> str:
    summary = _summary(result)
    status = "✅ Pass" if result.ok else "❌ Fail"
    lines = [
        "# RepoTruth report",
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
                f"| {item.severity.value} | `{item.code}` | `{_escape_table(location)}` | "
                f"{_escape_table(item.message)} |"
            )
    else:
        lines.append("No contract drift found.")
    return "\n".join(lines) + "\n"


def render_sarif(result: ScanResult, root: Path) -> str:
    findings = result.sorted_findings()
    rules: dict[str, dict[str, Any]] = {}
    sarif_results: list[dict[str, Any]] = []
    for item in findings:
        rules.setdefault(
            item.code,
            {
                "id": item.code,
                "name": item.code,
                "shortDescription": {"text": item.message},
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
                        "artifactLocation": {"uri": _relative(item.location.path, root)},
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
                        "name": "RepoTruth",
                        "version": __version__,
                        "informationUri": "https://github.com/xixvivji/RepoTruth",
                        "rules": [rules[key] for key in sorted(rules)],
                    }
                },
                "results": sarif_results,
            }
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _sarif_level(severity: Severity) -> str:
    return {
        Severity.ERROR: "error",
        Severity.WARNING: "warning",
        Severity.NOTE: "note",
    }[severity]


def render(result: ScanResult, root: Path, format_name: str) -> str:
    renderers = {
        "text": render_text,
        "json": render_json,
        "markdown": render_markdown,
        "sarif": render_sarif,
    }
    return renderers[format_name](result, root)
