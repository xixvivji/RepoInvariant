"""Configurable finding severity policy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from repotruth.models import ScanResult, Severity


def apply_rule_policy(result: ScanResult, config: Mapping[str, Any]) -> ScanResult:
    """Apply top-level ``rules`` overrides without mutating finding evidence."""

    configured = config.get("rules")
    rules = configured if isinstance(configured, Mapping) else {}
    findings = []
    for finding in result.findings:
        value = rules.get(finding.code)
        if value == "off":
            continue
        severity = Severity(value) if value in {"error", "warning"} else finding.severity
        findings.append(replace(finding, severity=severity))
    result.findings = findings
    result.findings = result.sorted_findings()
    return result
