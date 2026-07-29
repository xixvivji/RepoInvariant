"""Trace requirement IDs from Markdown, through OpenAPI, to tests."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

import regex
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from repoinvariant.filesystem import MAX_SCAN_FILES, read_limited_text
from repoinvariant.models import Finding, Location, ScanResult, Severity
from repoinvariant.policy import apply_rule_policy

DEFAULT_ID_PATTERN = r"\bREQ-[A-Z0-9][A-Z0-9-]*\b"
DEFAULT_OPENAPI_EXTENSION = "x-feature-id"

_ALWAYS_IGNORED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)")
_LIST_RE = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_SETEXT_RE = re.compile(r"^\s{0,3}(?:=+|-+)\s*$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,127}$", re.ASCII)
_MAX_MATCHES_PER_VALUE = 10_000
_MATCH_TIMEOUT_SECONDS = 0.05
_TOTAL_MATCH_BUDGET_SECONDS = 1.0
_MAX_TOTAL_MATCHES = 100_000
_MAX_YAML_DEPTH = 128
_MAX_YAML_NODES = 20_000


class _TraceFileError(ValueError):
    """Raised for an unsafe file matched by an otherwise valid pattern."""


class _MatchBudget:
    __slots__ = ("deadline", "matches")

    def __init__(self) -> None:
        self.deadline = time.monotonic() + _TOTAL_MATCH_BUDGET_SECONDS
        self.matches = 0

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Traceability id_pattern exceeded the total matching time budget")
        return min(_MATCH_TIMEOUT_SECONDS, remaining)

    def record_match(self) -> None:
        self.matches += 1
        if self.matches > _MAX_TOTAL_MATCHES:
            raise ValueError("Traceability id_pattern produced too many total matches")


def scan_traceability(root: Path, config_mapping: Mapping[str, Any]) -> ScanResult:
    """Check configured requirement, specification, and test files for trace gaps.

    Paths stored in the returned result are relative to *root*. Invalid, unreadable,
    hidden, virtual-environment, and binary files are skipped.
    """

    result = ScanResult()
    root = root.resolve()
    features = _feature_config(config_mapping)
    if not root.is_dir() or not features:
        return result

    try:
        pattern_source = str(features.get("id_pattern", DEFAULT_ID_PATTERN))
        id_pattern = regex.compile(pattern_source)
    except regex.error as error:
        raise ValueError(f"Invalid traceability id_pattern: {error}") from error

    extension = str(features.get("openapi_extension", DEFAULT_OPENAPI_EXTENSION))
    requirements_mode = str(features.get("requirements_mode", "definitions"))
    ignore = _as_patterns(features.get("ignore", ()))
    text_cache: dict[Path, str | None] = {}
    match_budget = _MatchBudget()

    requirement_paths = _glob_files(root, features.get("requirements"), ignore)
    specification_paths = _glob_files(root, features.get("specifications"), ignore)
    test_paths = _glob_files(root, features.get("tests"), ignore)

    requirement_occurrences: dict[str, list[_RequirementOccurrence]] = defaultdict(list)
    specification_occurrences: dict[str, list[Location]] = defaultdict(list)
    tested_ids: set[str] = set()

    for path in requirement_paths:
        text = _read_text(root, path, text_cache)
        if text is None:
            continue
        relative = path.relative_to(root)
        result.scanned_files.add(relative)
        for occurrence in _requirement_ids(relative, text, id_pattern, match_budget):
            if requirements_mode == "definitions" and not occurrence.is_definition:
                continue
            requirement_occurrences[occurrence.identifier].append(occurrence)

    for path in specification_paths:
        text = _read_text(root, path, text_cache)
        if text is None:
            continue
        relative = path.relative_to(root)
        result.scanned_files.add(relative)
        for identifier, location in _specification_ids(
            relative, text, id_pattern, extension, match_budget
        ):
            specification_occurrences[identifier].append(location)

    for path in test_paths:
        text = _read_text(root, path, text_cache)
        if text is None:
            continue
        relative = path.relative_to(root)
        result.scanned_files.add(relative)
        tested_ids.update(
            match.group(0) for match in _identifier_matches(id_pattern, text, match_budget)
        )

    requirement_locations = {
        identifier: _canonical_requirement_location(occurrences)
        for identifier, occurrences in requirement_occurrences.items()
    }
    specification_locations = {
        identifier: min(locations, key=_location_key)
        for identifier, locations in specification_occurrences.items()
    }

    requirement_ids = set(requirement_locations)
    specification_ids = set(specification_locations)
    all_ids = sorted(requirement_ids | specification_ids)
    reported_ids = {
        identifier: identifier if pattern_source == DEFAULT_ID_PATTERN else f"custom-id-{index}"
        for index, identifier in enumerate(all_ids, start=1)
    }

    for identifier in sorted(requirement_ids - specification_ids):
        reported = reported_ids[identifier]
        result.findings.append(
            Finding(
                code="TRACE001",
                message=f"Requirement '{reported}' is missing from the specification.",
                severity=Severity.ERROR,
                location=requirement_locations[identifier],
                hint=f"Add the {reported} value to {extension} on the implementing operation.",
            )
        )

    for identifier in sorted(specification_ids - requirement_ids):
        reported = reported_ids[identifier]
        result.findings.append(
            Finding(
                code="TRACE002",
                message=f"Specification references unknown requirement '{reported}'.",
                severity=Severity.ERROR,
                location=specification_locations[identifier],
                hint="Define the requirement or remove the stale specification reference.",
            )
        )

    for identifier in sorted(specification_ids - tested_ids):
        reported = reported_ids[identifier]
        result.findings.append(
            Finding(
                code="TRACE003",
                message=f"Specification feature '{reported}' has no matching test.",
                severity=Severity.ERROR,
                location=specification_locations[identifier],
                hint=f"Reference {reported} in a configured test file.",
            )
        )

    for identifier in sorted(requirement_occurrences):
        definitions = sorted(
            {
                occurrence.location
                for occurrence in requirement_occurrences[identifier]
                if occurrence.is_definition
            },
            key=_location_key,
        )
        if len(definitions) < 2:
            continue
        reported = reported_ids[identifier]
        result.findings.append(
            Finding(
                code="TRACE004",
                message=f"Requirement '{reported}' is defined more than once.",
                severity=Severity.WARNING,
                location=definitions[1],
                hint="Keep one canonical requirement definition and link to it elsewhere.",
                related=(definitions[0], *definitions[2:]),
            )
        )

    return apply_rule_policy(result, config_mapping)


class _RequirementOccurrence:
    __slots__ = ("identifier", "is_definition", "location")

    def __init__(self, identifier: str, location: Location, *, is_definition: bool) -> None:
        self.identifier = identifier
        self.location = location
        self.is_definition = is_definition


def _feature_config(config_mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    value = config_mapping.get("features")
    if isinstance(value, Mapping):
        return value
    # Accept an already-selected features mapping for library callers.
    if any(
        key in config_mapping for key in ("requirements", "specifications", "tests", "id_pattern")
    ):
        return config_mapping
    return {}


def _as_patterns(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, Path)):
        return (str(value),)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(str(item) for item in value if isinstance(item, (str, Path)))
    return ()


def _expand_braces(pattern: str) -> Iterable[str]:
    """Expand simple shell-style ``*.{yaml,yml,json}`` alternatives."""

    match = re.search(r"\{([^{}]+)\}", pattern)
    if match is None:
        yield pattern
        return
    for alternative in match.group(1).split(","):
        replacement = pattern[: match.start()] + alternative + pattern[match.end() :]
        yield from _expand_braces(replacement)


def _glob_files(root: Path, configured: Any, ignore: tuple[str, ...]) -> tuple[Path, ...]:
    found: set[Path] = set()
    for raw_pattern in _as_patterns(configured):
        normalized = raw_pattern.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized:
            continue
        normalized_path = PurePosixPath(normalized)
        if (
            normalized.startswith("/")
            or (len(normalized) >= 3 and normalized[1:3] == ":/")
            or ".." in normalized_path.parts
        ):
            raise ValueError("Traceability file patterns must stay inside the repository root")
        patterns: list[str] = []
        for expanded in _expand_braces(normalized):
            patterns.append(expanded)
            if len(patterns) > 32:
                raise ValueError("Traceability brace expansion exceeds 32 patterns")
        for pattern in patterns:
            try:
                candidates = root.glob(pattern)
                for candidate in candidates:
                    try:
                        relative = candidate.relative_to(root)
                    except ValueError:
                        continue
                    if _is_ignored(relative, ignore):
                        continue
                    if candidate.is_symlink():
                        raise _TraceFileError(
                            f"Configured traceability file must not be a symbolic link: {relative}"
                        )
                    if not candidate.is_file():
                        continue
                    try:
                        candidate.resolve().relative_to(root)
                    except ValueError:
                        continue
                    found.add(candidate)
                    if len(found) > MAX_SCAN_FILES:
                        raise _TraceFileError(
                            f"traceability file discovery exceeds {MAX_SCAN_FILES} files"
                        )
            except _TraceFileError:
                raise
            except (OSError, ValueError, NotImplementedError) as exc:
                raise ValueError(f"Invalid traceability file pattern {pattern!r}") from exc
    return tuple(sorted(found, key=lambda item: item.relative_to(root).as_posix()))


def _is_ignored(path: Path, configured: tuple[str, ...]) -> bool:
    parts = path.parts
    if any(part.startswith(".") or part in _ALWAYS_IGNORED_PARTS for part in parts):
        return True
    relative = path.as_posix()
    return any(_matches_ignore(relative, pattern) for pattern in configured)


def _matches_ignore(relative: str, raw_pattern: str) -> bool:
    pattern = raw_pattern.replace("\\", "/").strip()
    if pattern.startswith("./"):
        pattern = pattern[2:]
    pattern = pattern.rstrip("/")
    if not pattern:
        return False

    candidates = (pattern, pattern[3:]) if pattern.startswith("**/") else (pattern,)
    path = PurePosixPath(relative)
    for candidate in candidates:
        if fnmatchcase(relative, candidate) or path.match(candidate):
            return True
        if "/" not in candidate and any(fnmatchcase(part, candidate) for part in path.parts):
            return True
        if relative == candidate or relative.startswith(candidate + "/"):
            return True
    return False


def _read_text(root: Path, path: Path, cache: dict[Path, str | None]) -> str | None:
    if path in cache:
        return cache[path]
    try:
        text = read_limited_text(path, root=root)
    except UnicodeDecodeError as exc:
        raise ValueError(f"Invalid UTF-8 in configured file: {path.relative_to(root)}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read configured file: {path.relative_to(root)}") from exc
    if "\x00" in text:
        cache[path] = None
        return None
    cache[path] = text
    return text


def _identifier_matches(
    id_pattern: Any, text: str, budget: _MatchBudget
) -> tuple[Any, ...]:
    matches: list[Any] = []
    try:
        for match in id_pattern.finditer(text, timeout=budget.remaining()):
            matches.append(match)
            budget.record_match()
            if len(matches) > _MAX_MATCHES_PER_VALUE:
                raise ValueError("Traceability id_pattern produced too many matches")
    except TimeoutError as exc:
        raise ValueError("Traceability id_pattern matching timed out") from exc
    for match in matches:
        identifier = match.group(0)
        if (
            not _SAFE_IDENTIFIER_RE.fullmatch(identifier)
            or not any(separator in identifier for separator in "-_.:")
        ):
            raise ValueError(
                "Traceability id_pattern matched an unsafe identifier; "
                "use 2-128 ASCII letters, digits, '.', '_', ':', or '-' with a separator"
            )
    return tuple(matches)


def _requirement_ids(
    path: Path, text: str, id_pattern: Any, budget: _MatchBudget
) -> Iterable[_RequirementOccurrence]:
    lines = text.splitlines()
    fence: str | None = None
    in_comment = False

    for index, original_line in enumerate(lines):
        fence_match = _FENCE_RE.match(original_line)
        if fence_match:
            marker = fence_match.group(1)
            marker_kind = marker[0]
            if fence is None:
                fence = marker_kind
            elif fence == marker_kind:
                fence = None
            continue
        if fence is not None:
            continue

        line, in_comment = _without_html_comments(original_line, in_comment)
        if not line:
            continue
        for match in _identifier_matches(id_pattern, line, budget):
            location = Location(path=path, line=index + 1, column=match.start() + 1)
            yield _RequirementOccurrence(
                match.group(0),
                location,
                is_definition=_is_definition_line(lines, index, line, match),
            )


def _without_html_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Replace comments with spaces so identifier columns remain source-accurate."""

    visible: list[str] = []
    cursor = 0
    while cursor < len(line):
        if in_comment:
            end = line.find("-->", cursor)
            if end < 0:
                visible.append(" " * (len(line) - cursor))
                return "".join(visible), True
            visible.append(" " * (end + 3 - cursor))
            cursor = end + 3
            in_comment = False
            continue
        start = line.find("<!--", cursor)
        if start < 0:
            visible.append(line[cursor:])
            break
        visible.append(line[cursor:start])
        cursor = start
        in_comment = True
    return "".join(visible), in_comment


def _is_definition_line(lines: list[str], index: int, line: str, match: Any) -> bool:
    if _HEADING_RE.match(line):
        return True
    if index + 1 < len(lines) and _SETEXT_RE.match(lines[index + 1]):
        before = line[: match.start()]
        if not before.strip(" \t*_`"):
            return True

    list_match = _LIST_RE.match(line)
    if list_match is not None and match.start() >= list_match.end():
        before = line[list_match.end() : match.start()]
        after = line[match.end() :]
        if not before.strip(" \t*_`[(") and not re.match(r"\s*[])]\(", after):
            return _has_definition_body(after)

    stripped = line.lstrip()
    offset = len(line) - len(stripped)
    starts_definition = match.start() == offset or not line[offset : match.start()].strip("*_`[")
    if starts_definition and _has_definition_body(line[match.end() :]):
        return True

    if stripped.startswith("|"):
        first_cell_start = line.find("|") + 1
        first_cell_end = line.find("|", first_cell_start)
        if first_cell_end >= 0 and first_cell_start <= match.start() < first_cell_end:
            return True
    return False


def _has_definition_body(after_identifier: str) -> bool:
    remainder = after_identifier.lstrip(" \t*_`]")
    if not remainder or remainder.startswith("("):
        return False
    if remainder[0] in ":;\u2013\u2014":
        return bool(remainder[1:].strip())
    if remainder.startswith("- "):
        return bool(remainder[2:].strip())
    return False


def _canonical_requirement_location(
    occurrences: list[_RequirementOccurrence],
) -> Location:
    definitions = [item.location for item in occurrences if item.is_definition]
    candidates = definitions or [item.location for item in occurrences]
    return min(candidates, key=_location_key)


def _specification_ids(
    path: Path,
    text: str,
    id_pattern: Any,
    extension: str,
    budget: _MatchBudget,
) -> Iterable[tuple[str, Location]]:
    try:
        documents = tuple(yaml.compose_all(text, Loader=yaml.SafeLoader))
        lines = text.splitlines()
        seen: set[int] = set()
        for document in documents:
            if document is not None:
                yield from _walk_openapi_node(
                    document, path, lines, id_pattern, extension, budget, seen, depth=0
                )
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        position = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ValueError(f"Invalid YAML/JSON specification {path}{position}") from exc
    except RecursionError as exc:
        raise ValueError(f"Specification nesting is too deep in {path}") from exc


def _walk_openapi_node(
    node: Node,
    path: Path,
    lines: list[str],
    id_pattern: Any,
    extension: str,
    budget: _MatchBudget,
    seen: set[int],
    depth: int,
) -> Iterable[tuple[str, Location]]:
    if depth > _MAX_YAML_DEPTH:
        raise ValueError(f"Specification nesting exceeds {_MAX_YAML_DEPTH} levels in {path}")
    node_identity = id(node)
    if node_identity in seen:
        return
    seen.add(node_identity)
    if len(seen) > _MAX_YAML_NODES:
        raise ValueError(f"Specification exceeds {_MAX_YAML_NODES} YAML nodes in {path}")

    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            if isinstance(key_node, ScalarNode) and key_node.value == extension:
                yield from _ids_from_extension_value(
                    value_node, path, lines, id_pattern, budget, seen=set(), depth=0
                )
            yield from _walk_openapi_node(
                value_node, path, lines, id_pattern, extension, budget, seen, depth + 1
            )
    elif isinstance(node, SequenceNode):
        for child in node.value:
            yield from _walk_openapi_node(
                child, path, lines, id_pattern, extension, budget, seen, depth + 1
            )


def _ids_from_extension_value(
    node: Node,
    path: Path,
    lines: list[str],
    id_pattern: Any,
    budget: _MatchBudget,
    seen: set[int],
    depth: int,
) -> Iterable[tuple[str, Location]]:
    if depth > _MAX_YAML_DEPTH:
        raise ValueError(f"Specification extension nesting exceeds {_MAX_YAML_DEPTH} levels")
    identity = id(node)
    if identity in seen:
        return
    seen.add(identity)
    if len(seen) > _MAX_YAML_NODES:
        raise ValueError("Specification extension contains too many YAML nodes")
    if isinstance(node, ScalarNode):
        if node.tag != "tag:yaml.org,2002:str":
            return
        for match in _identifier_matches(id_pattern, node.value, budget):
            identifier = match.group(0)
            yield identifier, _node_identifier_location(path, lines, node, identifier)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            yield from _ids_from_extension_value(
                child, path, lines, id_pattern, budget, seen=seen, depth=depth + 1
            )


def _node_identifier_location(
    path: Path, lines: list[str], node: ScalarNode, identifier: str
) -> Location:
    start_line = node.start_mark.line
    end_line = min(node.end_mark.line, len(lines) - 1)
    for line_index in range(start_line, end_line + 1):
        start_column = node.start_mark.column if line_index == start_line else 0
        column = lines[line_index].find(identifier, start_column)
        if column >= 0:
            return Location(path=path, line=line_index + 1, column=column + 1)
    return Location(
        path=path,
        line=node.start_mark.line + 1,
        column=node.start_mark.column + 1,
    )


def _location_key(location: Location) -> tuple[str, int, int]:
    return location.path.as_posix(), location.line, location.column
