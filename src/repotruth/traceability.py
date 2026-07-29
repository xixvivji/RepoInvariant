"""Trace requirement IDs from Markdown, through OpenAPI, to tests."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from repotruth.models import Finding, Location, ScanResult, Severity

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
        id_pattern = re.compile(str(features.get("id_pattern", DEFAULT_ID_PATTERN)))
    except re.error as error:
        raise ValueError(f"Invalid traceability id_pattern: {error}") from error

    extension = str(features.get("openapi_extension", DEFAULT_OPENAPI_EXTENSION))
    ignore = _as_patterns(features.get("ignore", ()))
    text_cache: dict[Path, str | None] = {}

    requirement_paths = _glob_files(root, features.get("requirements"), ignore)
    specification_paths = _glob_files(root, features.get("specifications"), ignore)
    test_paths = _glob_files(root, features.get("tests"), ignore)

    requirement_occurrences: dict[str, list[_RequirementOccurrence]] = defaultdict(list)
    specification_occurrences: dict[str, list[Location]] = defaultdict(list)
    tested_ids: set[str] = set()

    for path in requirement_paths:
        text = _read_text(path, text_cache)
        if text is None:
            continue
        relative = path.relative_to(root)
        result.scanned_files.add(relative)
        for occurrence in _requirement_ids(relative, text, id_pattern):
            requirement_occurrences[occurrence.identifier].append(occurrence)

    for path in specification_paths:
        text = _read_text(path, text_cache)
        if text is None:
            continue
        relative = path.relative_to(root)
        result.scanned_files.add(relative)
        for identifier, location in _specification_ids(relative, text, id_pattern, extension):
            specification_occurrences[identifier].append(location)

    for path in test_paths:
        text = _read_text(path, text_cache)
        if text is None:
            continue
        relative = path.relative_to(root)
        result.scanned_files.add(relative)
        tested_ids.update(match.group(0) for match in id_pattern.finditer(text))

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

    for identifier in sorted(requirement_ids - specification_ids):
        result.findings.append(
            Finding(
                code="TRACE001",
                message=f"Requirement '{identifier}' is missing from the specification.",
                severity=Severity.ERROR,
                location=requirement_locations[identifier],
                hint=f"Add {extension}: {identifier} to the implementing OpenAPI operation.",
            )
        )

    for identifier in sorted(specification_ids - requirement_ids):
        result.findings.append(
            Finding(
                code="TRACE002",
                message=f"Specification references unknown requirement '{identifier}'.",
                severity=Severity.ERROR,
                location=specification_locations[identifier],
                hint="Define the requirement or remove the stale specification reference.",
            )
        )

    for identifier in sorted(specification_ids - tested_ids):
        result.findings.append(
            Finding(
                code="TRACE003",
                message=f"Specification feature '{identifier}' has no matching test.",
                severity=Severity.ERROR,
                location=specification_locations[identifier],
                hint=f"Reference {identifier} in a configured test file.",
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
        result.findings.append(
            Finding(
                code="TRACE004",
                message=f"Requirement '{identifier}' is defined more than once.",
                severity=Severity.WARNING,
                location=definitions[1],
                hint="Keep one canonical requirement definition and link to it elsewhere.",
                related=(definitions[0], *definitions[2:]),
            )
        )

    result.findings = result.sorted_findings()
    return result


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
        for pattern in _expand_braces(normalized):
            try:
                candidates = root.glob(pattern)
                for candidate in candidates:
                    if not candidate.is_file():
                        continue
                    try:
                        candidate.resolve().relative_to(root)
                        relative = candidate.relative_to(root)
                    except ValueError:
                        continue
                    if _is_ignored(relative, ignore):
                        continue
                    found.add(candidate)
            except (OSError, ValueError):
                continue
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


def _read_text(path: Path, cache: dict[Path, str | None]) -> str | None:
    if path in cache:
        return cache[path]
    try:
        data = path.read_bytes()
    except OSError:
        cache[path] = None
        return None
    if b"\x00" in data:
        cache[path] = None
        return None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        cache[path] = None
        return None
    cache[path] = text
    return text


def _requirement_ids(
    path: Path, text: str, id_pattern: re.Pattern[str]
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
        for match in id_pattern.finditer(line):
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


def _is_definition_line(lines: list[str], index: int, line: str, match: re.Match[str]) -> bool:
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
    id_pattern: re.Pattern[str],
    extension: str,
) -> Iterable[tuple[str, Location]]:
    try:
        documents = tuple(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        yield from _fallback_specification_ids(path, text, id_pattern, extension)
        return

    lines = text.splitlines()
    seen: set[int] = set()
    for document in documents:
        if document is not None:
            yield from _walk_openapi_node(document, path, lines, id_pattern, extension, seen)


def _walk_openapi_node(
    node: Node,
    path: Path,
    lines: list[str],
    id_pattern: re.Pattern[str],
    extension: str,
    seen: set[int],
) -> Iterable[tuple[str, Location]]:
    node_identity = id(node)
    if node_identity in seen:
        return
    seen.add(node_identity)

    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            if isinstance(key_node, ScalarNode) and key_node.value == extension:
                yield from _ids_from_extension_value(value_node, path, lines, id_pattern)
            yield from _walk_openapi_node(value_node, path, lines, id_pattern, extension, seen)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            yield from _walk_openapi_node(child, path, lines, id_pattern, extension, seen)


def _ids_from_extension_value(
    node: Node,
    path: Path,
    lines: list[str],
    id_pattern: re.Pattern[str],
) -> Iterable[tuple[str, Location]]:
    if isinstance(node, ScalarNode):
        if node.tag != "tag:yaml.org,2002:str":
            return
        for match in id_pattern.finditer(node.value):
            identifier = match.group(0)
            yield identifier, _node_identifier_location(path, lines, node, identifier)
    elif isinstance(node, SequenceNode):
        for child in node.value:
            yield from _ids_from_extension_value(child, path, lines, id_pattern)


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


def _fallback_specification_ids(
    path: Path,
    text: str,
    id_pattern: re.Pattern[str],
    extension: str,
) -> Iterable[tuple[str, Location]]:
    key_pattern = re.compile(
        rf"^(?P<indent>\s*)[\"']?{re.escape(extension)}[\"']?\s*:\s*(?P<value>.*)$"
    )
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        key_match = key_pattern.match(lines[index])
        if key_match is None:
            index += 1
            continue
        base_indent = len(key_match.group("indent"))
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= base_indent:
                break
            end += 1
        for line_index in range(index, end):
            for match in id_pattern.finditer(lines[line_index]):
                yield match.group(0), Location(path, line_index + 1, match.start() + 1)
        index = end


def _location_key(location: Location) -> tuple[str, int, int]:
    return location.path.as_posix(), location.line, location.column
