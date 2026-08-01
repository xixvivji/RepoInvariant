"""Reusable deterministic resource limits for repository scanners."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

_Item = TypeVar("_Item")


@dataclass(frozen=True, slots=True)
class ScanLimits:
    """Hard limits for one scanner invocation."""

    max_files: int
    max_input_bytes: int
    max_items: int
    max_findings: int
    max_related_locations: int
    max_report_bytes: int

    def __post_init__(self) -> None:
        positive = (
            self.max_files,
            self.max_input_bytes,
            self.max_items,
            self.max_findings,
            self.max_report_bytes,
        )
        if any(type(value) is not int or value <= 0 for value in positive):
            raise ValueError("scan limits must be positive integers")
        if (
            type(self.max_related_locations) is not int
            or self.max_related_locations < 0
        ):
            raise ValueError("related-location limit must be a non-negative integer")


@dataclass(slots=True)
class ScanBudget:
    """Track bounded scanner work without depending on scanner-specific models."""

    label: str
    item_label: str
    limits: ScanLimits
    files: set[Path] = field(default_factory=set)
    input_bytes: int = 0
    items: int = 0
    findings: int = 0
    report_bytes: int = 0

    @property
    def remaining_input_bytes(self) -> int:
        return self.limits.max_input_bytes - self.input_bytes

    def record_file(self, path: Path) -> None:
        self.files.add(path)
        if len(self.files) > self.limits.max_files:
            raise ValueError(
                f"{self.label} scan exceeds {self.limits.max_files} unique files"
            )

    def record_input_bytes(self, size: int) -> None:
        if type(size) is not int or size < 0:
            raise ValueError("input byte count must be a non-negative integer")
        total = self.input_bytes + size
        if total > self.limits.max_input_bytes:
            raise ValueError(
                f"{self.label} scan exceeds "
                f"{self.limits.max_input_bytes} total input bytes"
            )
        self.input_bytes = total

    def record_item(self) -> None:
        total = self.items + 1
        if total > self.limits.max_items:
            raise ValueError(
                f"{self.label} scan exceeds {self.limits.max_items} {self.item_label}"
            )
        self.items = total

    def bound_related(self, items: Sequence[_Item]) -> tuple[tuple[_Item, ...], int]:
        selected = tuple(items[: self.limits.max_related_locations])
        return selected, len(items) - len(selected)

    def record_finding(self, encoded_bytes: int) -> None:
        if type(encoded_bytes) is not int or encoded_bytes < 0:
            raise ValueError("report byte count must be a non-negative integer")
        findings = self.findings + 1
        if findings > self.limits.max_findings:
            raise ValueError(
                f"{self.label} scan exceeds {self.limits.max_findings} findings"
            )
        report_bytes = self.report_bytes + encoded_bytes
        if report_bytes > self.limits.max_report_bytes:
            raise ValueError(
                f"{self.label} scan report exceeds "
                f"{self.limits.max_report_bytes} encoded finding bytes"
            )
        self.findings = findings
        self.report_bytes = report_bytes


__all__ = ["ScanBudget", "ScanLimits"]
