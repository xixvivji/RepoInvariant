"""Small, serializable domain models shared by scanners and reporters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class Severity(StrEnum):
    """Finding severity, ordered from informational to merge-blocking."""

    NOTE = "note"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Location:
    """A one-based source location inside the scanned repository."""

    path: Path
    line: int = 1
    column: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path.as_posix(), "line": self.line, "column": self.column}


@dataclass(frozen=True, slots=True)
class Finding:
    """A deterministic contract violation or warning."""

    code: str
    message: str
    severity: Severity
    location: Location | None = None
    hint: str | None = None
    related: tuple[Location, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
        }
        if self.location is not None:
            payload["location"] = self.location.as_dict()
        if self.hint is not None:
            payload["hint"] = self.hint
        if self.related:
            payload["related"] = [location.as_dict() for location in self.related]
        return payload


@dataclass(slots=True)
class ScanResult:
    """Aggregate scanner output used to decide the command exit status."""

    findings: list[Finding] = field(default_factory=list)
    scanned_files: set[Path] = field(default_factory=set)

    @property
    def error_count(self) -> int:
        return sum(finding.severity is Severity.ERROR for finding in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(finding.severity is Severity.WARNING for finding in self.findings)

    @property
    def ok(self) -> bool:
        return self.error_count == 0

    def extend(self, other: ScanResult) -> None:
        self.findings.extend(other.findings)
        self.scanned_files.update(other.scanned_files)

    def sorted_findings(self) -> list[Finding]:
        rank = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.NOTE: 2}
        return sorted(
            self.findings,
            key=lambda item: (
                rank[item.severity],
                item.location.path.as_posix() if item.location else "",
                item.location.line if item.location else 0,
                item.code,
                item.message,
            ),
        )
