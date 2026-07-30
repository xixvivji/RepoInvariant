"""Privacy-preserving scanner inventory used by the ``doctor`` command."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from repoinvariant.filesystem import MAX_SCAN_FILES


def _relative_path(path: Path) -> Path:
    """Return a normalized repository-relative path or fail closed."""

    normalized = path.as_posix().removeprefix("./")
    candidate = PurePosixPath(normalized)
    if not normalized or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("diagnostic paths must stay inside the repository")
    return Path(*candidate.parts)


@dataclass(slots=True)
class DiagnosticBudget:
    """Bound unique diagnostic paths across every scanner in one doctor run."""

    paths: set[Path] = field(default_factory=set)

    def record(self, path: Path) -> None:
        self.paths.add(path)
        if len(self.paths) > MAX_SCAN_FILES:
            raise ValueError(
                f"doctor target and exclusion discovery exceeds {MAX_SCAN_FILES} unique files"
            )


@dataclass(slots=True)
class SourceDiagnostics:
    """Bounded, content-free discovery facts for one configured source range."""

    name: str
    patterns: tuple[str, ...]
    required: bool = False
    budget: DiagnosticBudget = field(default_factory=DiagnosticBudget, repr=False)
    matched_files: set[Path] = field(default_factory=set)
    derived_files: set[Path] = field(default_factory=set)
    ignored_files: dict[Path, str] = field(default_factory=dict)

    def record_matched(self, path: Path) -> None:
        relative = _relative_path(path)
        self.budget.record(relative)
        self.ignored_files.pop(relative, None)
        self.matched_files.add(relative)

    def record_derived(self, path: Path) -> None:
        relative = _relative_path(path)
        self.budget.record(relative)
        self.ignored_files.pop(relative, None)
        self.derived_files.add(relative)

    def record_ignored(self, path: Path, reason: str) -> None:
        if reason not in {"built_in_ignore", "configured_ignore", "binary"}:
            raise ValueError("unsupported diagnostic exclusion reason")
        relative = _relative_path(path)
        self.budget.record(relative)
        self.matched_files.discard(relative)
        self.derived_files.discard(relative)
        self.ignored_files.setdefault(relative, reason)


@dataclass(slots=True)
class ScannerDiagnostics:
    """Mutable event sink shared by a scanner and the ``doctor`` report builder."""

    name: str
    budget: DiagnosticBudget = field(default_factory=DiagnosticBudget, repr=False)
    sources: dict[str, SourceDiagnostics] = field(default_factory=dict)

    def source(
        self,
        name: str,
        patterns: tuple[str, ...],
        *,
        required: bool = False,
    ) -> SourceDiagnostics:
        existing = self.sources.get(name)
        if existing is not None:
            if existing.patterns != patterns or existing.required is not required:
                raise ValueError(f"diagnostic source {name!r} was configured inconsistently")
            return existing
        source = SourceDiagnostics(
            name=name,
            patterns=patterns,
            required=required,
            budget=self.budget,
        )
        self.sources[name] = source
        return source


__all__ = ["DiagnosticBudget", "ScannerDiagnostics", "SourceDiagnostics"]
