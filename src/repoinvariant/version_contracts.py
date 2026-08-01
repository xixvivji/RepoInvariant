"""Cross-check a canonical Java major across repository version declarations."""

from __future__ import annotations

import fnmatch
import io
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from repoinvariant.config import VERSION_JAVA_DEFAULTS, discover_files
from repoinvariant.diagnostics import ScannerDiagnostics, SourceDiagnostics
from repoinvariant.filesystem import (
    MAX_SCAN_BYTES,
    MAX_SCAN_FILES,
    contained_path,
    read_limited_text,
)
from repoinvariant.models import Finding, Location, ScanResult, Severity
from repoinvariant.policy import apply_rule_policy
from repoinvariant.resource_budget import ScanBudget, ScanLimits

_SOURCE_NAMES = (
    "gradle",
    "maven",
    "version_files",
    "dockerfiles",
    "compose",
    "workflows",
    "docs",
)
_SOURCE_LABELS = {
    "gradle": "Gradle",
    "maven": "Maven",
    "version_files": "Java version file",
    "dockerfiles": "Dockerfile",
    "compose": "Compose",
    "workflows": "GitHub Actions",
    "docs": "documentation",
}
_JAVA_MAJOR_RE = re.compile(r"^[1-9][0-9]{0,2}$", re.ASCII)
_VERSION_LITERAL_RE = re.compile(
    r"^(?P<major>[1-9][0-9]{0,2})"
    r"(?:(?:\.(?:[0-9]+|[xX*]))+|[uU][0-9]+)?"
    r"(?:[-+_][A-Za-z0-9][A-Za-z0-9._+-]*)?$",
    re.ASCII,
)
_LEGACY_VERSION_RE = re.compile(
    r"^1\.(?P<major>[1-9][0-9]{0,2})(?:\.[0-9]+)*(?:[-+_][A-Za-z0-9._+-]+)?$",
    re.ASCII,
)
_DISTRIBUTION_VERSION_RE = re.compile(
    r"(?:^|[-_@])(?P<version>(?:1\.)?[1-9][0-9]{0,2}(?:\.[0-9]+)*)"
    r"(?:$|[-+_])",
    re.ASCII,
)
_JAVA_VERSION_MARKER_RE = re.compile(
    r"(?:^|[-_@])"
    r"(?:java|jdk|jre|openjdk(?:64)?|graalvm(?:64)?|temurin|corretto|"
    r"amazoncorretto|zulu|liberica|sapmachine|semeru|ibm-semeru)"
    r"[-_]?(?P<major>[1-9][0-9]{0,2})(?:$|[-_.+])",
    re.IGNORECASE | re.ASCII,
)
_AMBIGUOUS_VERSION_FILE_RE = re.compile(
    r"(?:^|[-_])(?:or|and)[-_](?:1\.)?[1-9][0-9]{0,2}(?:$|[-_.+])",
    re.IGNORECASE | re.ASCII,
)
_GRADLE_CALL_RE = re.compile(
    r"\b(?:JavaLanguageVersion\s*\.\s*of|jvmToolchain)\s*\((?P<value>[^)]*)\)"
)
_GRADLE_COMPATIBILITY_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:(?:java)\s*\.\s*)?"
    r"(?P<name>sourceCompatibility|targetCompatibility)"
    r"(?P<operator>\s*=\s*|[ \t]+)"
    r"(?P<value>[^\s;,}\r\n]+)",
    re.ASCII,
)
_GRADLE_JAVA_VERSION_RE = re.compile(
    r"^JavaVersion\s*\.\s*VERSION_(?:(?:1_)?(?P<major>[1-9][0-9]{0,2}))$",
    re.ASCII,
)
_DOCKER_ARG_RE = re.compile(
    r"^\s*ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*=\s*(?P<value>.*?))?\s*$",
    re.IGNORECASE,
)
_DOCKER_FROM_RE = re.compile(
    r"^\s*FROM(?:\s+--[^\s]+)*\s+(?P<image>[^\s]+)",
    re.IGNORECASE,
)
_ARG_REFERENCE_RE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|"
    r"(?P<plain>[A-Za-z_][A-Za-z0-9_]*))"
)
_EMBEDDED_JAVA_TAG_RE = re.compile(
    r"(?:^|[-_])(?:jdk|jre|open|temurin|corretto|amazoncorretto|openjdk|zulu|"
    r"liberica|sapmachine|semeru|ibm-semeru)"
    r"[-_]?(?P<major>[1-9][0-9]{0,2})(?:$|[-_.+])",
    re.IGNORECASE,
)
_LEADING_IMAGE_VERSION_RE = re.compile(
    r"^(?P<major>[1-9][0-9]{0,2})(?:$|[._+\-uU])",
    re.ASCII,
)
_DOC_DECLARATION_RE = re.compile(
    r"^\s{0,3}(?:[-+*]\s+)?(?:\*\*|__|`)?"
    r"(?P<label>java(?:[-_\s]+version)?|jdk(?:[-_\s]+version)?)"
    r"\s*(?P<separator>[:=])(?:\*\*|__|`)?\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
_DOC_TABLE_RE = re.compile(
    r"^\s*\|\s*(?:\*\*|__|`)?"
    r"(?P<label>java(?:[-_\s]+version)?|jdk(?:[-_\s]+version)?)"
    r"(?:\*\*|__|`)?\s*\|\s*(?P<value>[^|]*?)\s*\|",
    re.IGNORECASE,
)
_DOC_VERSION_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:(?:1\.)?[1-9][0-9]{0,2}(?:\.[0-9]+)*)"
    r"(?:[-_][A-Za-z][A-Za-z0-9._+-]*|\+[A-Za-z0-9][A-Za-z0-9._+-]*)?"
    r"(?![A-Za-z0-9])",
    re.ASCII,
)
_DOC_RANGE_RE = re.compile(
    r"(?:\b(?:at\s+least|minimum|min(?:imum)?|or\s+(?:later|newer|higher)|"
    r"and\s+(?:later|newer|higher))\b|(?:^|\s)[<>]=?\s*(?:1\.)?[1-9][0-9]{0,2}|"
    r"(?<![0-9.])(?:1\.)?[1-9][0-9]{0,2}\s*\+(?![A-Za-z0-9]))",
    re.IGNORECASE | re.ASCII,
)
_MARKDOWN_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
_MAX_YAML_DEPTH = 100
_MAX_YAML_NODES = 20_000
_MAX_XML_NODES = 20_000
_MAX_XML_DEPTH = 100
_MAX_DECLARATIONS_PER_FILE = 10_000
_MAX_TOTAL_DECLARATIONS = 100_000
_MAX_DOCKER_IMAGE_CHARS = 4_096
_VERSION_SCAN_LIMITS = ScanLimits(
    max_files=MAX_SCAN_FILES,
    max_input_bytes=64 * 1024 * 1024,
    max_items=_MAX_TOTAL_DECLARATIONS,
    max_findings=_MAX_TOTAL_DECLARATIONS,
    max_related_locations=_MAX_DECLARATIONS_PER_FILE,
    max_report_bytes=64 * 1024 * 1024,
)

_KNOWN_JAVA_IMAGES = frozenset(
    {
        "amazoncorretto",
        "corretto",
        "eclipse-temurin",
        "eclipse-temurin-nightly",
        "graalvm-ce",
        "ibm-semeru-runtimes",
        "java",
        "jdk",
        "jdk-community",
        "liberica-openjdk",
        "liberica-openjdk-alpine",
        "openjdk",
        "sapmachine",
        "zulu-openjdk",
    }
)
_KNOWN_BUILD_IMAGES = frozenset({"gradle", "maven"})


@dataclass(frozen=True, slots=True)
class _Declaration:
    source: str
    location: Location
    major: str | None


@dataclass(slots=True)
class _OffsetLocator:
    """Resolve monotonically increasing offsets without repeatedly rescanning text."""

    root: Path
    path: Path
    text: str
    cursor: int = 0
    line: int = 1
    line_start: int = 0

    def location(self, offset: int) -> Location:
        if offset < self.cursor:
            raise ValueError("source offsets must be monotonically increasing")
        newlines = self.text.count("\n", self.cursor, offset)
        if newlines:
            self.line += newlines
            self.line_start = self.text.rfind("\n", self.cursor, offset) + 1
        self.cursor = offset
        return _location(
            self.root,
            self.path,
            self.line,
            offset - self.line_start + 1,
        )


def _append_declaration(
    declarations: list[_Declaration], declaration: _Declaration, path: Path
) -> None:
    """Append one declaration, failing as soon as the per-file bound is reached."""

    if len(declarations) >= _MAX_DECLARATIONS_PER_FILE:
        raise ValueError(
            f"version declarations exceed {_MAX_DECLARATIONS_PER_FILE} in one file: {path}"
        )
    declarations.append(declaration)


def _read_scan_text(root: Path, path: Path, budget: ScanBudget) -> str:
    try:
        return read_limited_text(
            path,
            root=root,
            max_bytes=MAX_SCAN_BYTES,
            budget=budget,
        )
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 in configured file: {path}") from exc


def _location(root: Path, path: Path, line: int, column: int = 1) -> Location:
    return Location(path=path.relative_to(root), line=line, column=column)


def _node_location(root: Path, path: Path, node: Node) -> Location:
    return _location(root, path, node.start_mark.line + 1, node.start_mark.column + 1)


def _yaml_documents(text: str, path: Path) -> Iterable[Node]:
    nodes = 0
    try:
        for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
            if document is None:
                continue
            nodes += _validate_yaml_graph(document, path)
            if nodes > _MAX_YAML_NODES:
                raise ValueError(f"YAML file exceeds {_MAX_YAML_NODES} nodes: {path}")
            yield document
    except (RecursionError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid YAML in configured file: {path}") from exc


def _validate_yaml_graph(document: Node, path: Path) -> int:
    seen: set[int] = set()
    active: set[int] = set()
    visits = 0

    def visit(node: Node, depth: int) -> None:
        nonlocal visits
        visits += 1
        if visits > _MAX_YAML_NODES:
            raise ValueError(
                f"YAML document exceeds {_MAX_YAML_NODES} nodes and references: {path}"
            )
        if depth > _MAX_YAML_DEPTH:
            raise ValueError(f"YAML nesting exceeds {_MAX_YAML_DEPTH} levels: {path}")
        identity = id(node)
        if identity in active:
            raise ValueError(f"YAML alias cycle is not supported: {path}")
        if identity in seen:
            return
        seen.add(identity)
        active.add(identity)
        try:
            if isinstance(node, MappingNode):
                scalar_keys: set[tuple[str, str]] = set()
                for key_node, value_node in node.value:
                    if isinstance(key_node, ScalarNode):
                        key = (key_node.tag, key_node.value)
                        if key in scalar_keys:
                            raise ValueError(
                                f"duplicate YAML mapping key is not supported: {path}"
                            )
                        scalar_keys.add(key)
                    visit(key_node, depth + 1)
                    visit(value_node, depth + 1)
            elif isinstance(node, SequenceNode):
                for child in node.value:
                    visit(child, depth + 1)
        finally:
            active.remove(identity)

    visit(document, 0)
    return visits


@dataclass(slots=True)
class _MappingResolver:
    """Resolve YAML mapping merges once per node and requested key."""

    cache: dict[tuple[int, str], tuple[Node, ...]] = field(default_factory=dict)
    active: set[tuple[int, str]] = field(default_factory=set)

    def values(self, node: Node, name: str) -> tuple[Node, ...]:
        if not isinstance(node, MappingNode):
            return ()
        key = (id(node), name)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        if key in self.active:
            return ()
        self.active.add(key)
        try:
            direct = tuple(
                value_node
                for key_node, value_node in node.value
                if isinstance(key_node, ScalarNode) and key_node.value == name
            )
            if direct:
                resolved = direct
            else:
                resolved = ()
                for key_node, value_node in node.value:
                    if not isinstance(key_node, ScalarNode) or key_node.value != "<<":
                        continue
                    candidates: Sequence[Node]
                    if isinstance(value_node, SequenceNode):
                        candidates = value_node.value
                    else:
                        candidates = (value_node,)
                    for candidate in candidates:
                        inherited = self.values(candidate, name)
                        if inherited:
                            # YAML merge sequences give earlier mappings precedence.
                            resolved = inherited
                            break
                    if resolved:
                        break
            self.cache[key] = resolved
            return resolved
        finally:
            self.active.remove(key)

    def value(self, node: Node, name: str) -> Node | None:
        values = self.values(node, name)
        return values[0] if values else None


def _major_from_literal(value: str) -> str | None:
    value = value.strip()
    start = 0
    end = len(value)
    while end - start >= 2 and value[start] == value[end - 1] and value[start] in {
        "'",
        '"',
        "`",
    }:
        start += 1
        end -= 1
        while start < end and value[start].isspace():
            start += 1
        while end > start and value[end - 1].isspace():
            end -= 1
    value = value[start:end]
    legacy = _LEGACY_VERSION_RE.fullmatch(value)
    if legacy is not None:
        return legacy.group("major")
    match = _VERSION_LITERAL_RE.fullmatch(value)
    return match.group("major") if match is not None else None


def parse_java_version_file_major(value: str) -> str | None:
    """Return the Java major declared by one supported version-file value."""

    stripped = value.strip()
    if _AMBIGUOUS_VERSION_FILE_RE.search(stripped):
        return None
    direct = _major_from_literal(value)
    if direct is not None:
        return direct
    marker = _JAVA_VERSION_MARKER_RE.search(stripped)
    if marker is not None:
        return marker.group("major")
    match = _DISTRIBUTION_VERSION_RE.search(stripped)
    return _major_from_literal(match.group("version")) if match is not None else None


def _mask_gradle_comments(text: str) -> str:
    output = list(text)
    quote: str | None = None
    triple_quote = False
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quote is not None:
            if triple_quote and text.startswith(quote * 3, index):
                output[index : index + 3] = " " * 3
                quote = None
                triple_quote = False
                index += 3
                continue
            if character != "\n":
                output[index] = " "
            if escaped:
                escaped = False
            elif not triple_quote and character == "\\":
                escaped = True
            elif not triple_quote and character == quote:
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            triple_quote = text.startswith(character * 3, index)
            width = 3 if triple_quote else 1
            output[index : index + width] = " " * width
            index += width
            continue
        if text.startswith("$/", index):
            end = text.find("/$", index + 2)
            end = len(text) if end < 0 else end + 2
            for position in range(index, end):
                if output[position] != "\n":
                    output[position] = " "
            index = end
            continue
        if character == "/" and following == "/":
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            output[index:end] = " " * (end - index)
            index = end
            continue
        if character == "/" and following == "*":
            end = text.find("*/", index + 2)
            end = len(text) if end < 0 else end + 2
            for position in range(index, end):
                if output[position] != "\n":
                    output[position] = " "
            index = end
            continue
        if character == "/":
            previous = index - 1
            while previous >= 0 and text[previous] in {" ", "\t", "\r"}:
                previous -= 1
            previous_character = text[previous] if previous >= 0 else "\n"
            word_start = previous
            while word_start >= 0 and (
                text[word_start].isalnum() or text[word_start] == "_"
            ):
                word_start -= 1
            previous_word = text[word_start + 1 : previous + 1]
            if (
                previous_character in "\n=([{,:;!?&|<>+-*%^~"
                or previous_word in {"assert", "case", "in", "return", "throw", "yield"}
            ):
                end = index + 1
                escaped_slash = False
                while end < len(text):
                    current = text[end]
                    if escaped_slash:
                        escaped_slash = False
                    elif current == "\\":
                        escaped_slash = True
                    elif current == "/":
                        end += 1
                        for position in range(index, end):
                            if output[position] != "\n":
                                output[position] = " "
                        index = end
                        break
                    end += 1
                if index != end:
                    for position in range(index, len(text)):
                        if output[position] != "\n":
                            output[position] = " "
                    index = len(text)
                continue
        index += 1
    return "".join(output)


def _gradle_compatibility_major(value: str) -> str | None:
    java_version = _GRADLE_JAVA_VERSION_RE.fullmatch(value.strip())
    if java_version is not None:
        return java_version.group("major")
    return _major_from_literal(value)


def _scan_gradle(root: Path, path: Path, budget: ScanBudget) -> list[_Declaration]:
    text = _read_scan_text(root, path, budget)
    searchable = _mask_gradle_comments(text)
    declarations: list[_Declaration] = []
    locator = _OffsetLocator(root, path, text)
    candidates: list[tuple[int, str, bool]] = []
    for match in _GRADLE_CALL_RE.finditer(text):
        # The call name remains visible only when it is code, not a comment or string.  Parse the
        # value from the original text so quoted literal majors remain supported.
        if not searchable[match.start() : match.start("value")].strip():
            continue
        raw_value = match.group("value")
        value_offset = match.start("value") + len(raw_value) - len(raw_value.lstrip())
        candidates.append((value_offset, raw_value, False))
    for match in _GRADLE_COMPATIBILITY_RE.finditer(text):
        # Comments and string literals are blanked in ``searchable`` without moving offsets.
        if not searchable[match.start("name") : match.end("name")].strip():
            continue
        line_start = searchable.rfind("\n", 0, match.start("name")) + 1
        prefix = searchable[line_start : match.start("name")].rstrip()
        # A bare or qualified property assignment is contract evidence. A preceding type-like
        # token instead declares a local/helper variable that merely shares the Gradle property
        # name (for example ``String sourceCompatibility = ...``).
        if prefix and (prefix[-1].isalnum() or prefix[-1] in {"_", "$", "]", ">"}):
            continue
        raw_value = match.group("value")
        candidates.append((match.start("value"), raw_value, True))

    for value_offset, raw_value, compatibility in sorted(candidates):
        _append_declaration(
            declarations,
            _Declaration(
                source="gradle",
                location=locator.location(value_offset),
                major=(
                    _gradle_compatibility_major(raw_value)
                    if compatibility
                    else _major_from_literal(raw_value)
                ),
            ),
            path,
        )
    return declarations


def _xml_local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _mask_xml_non_elements(text: str) -> str:
    output = list(text)
    for pattern in (
        re.compile(r"<!--[\s\S]*?-->"),
        re.compile(r"<!\[CDATA\[[\s\S]*?\]\]>", re.IGNORECASE),
        re.compile(r"<\?(?!xml\b)[\s\S]*?\?>", re.IGNORECASE),
    ):
        for match in pattern.finditer(text):
            for index in range(match.start(), match.end()):
                if output[index] != "\n":
                    output[index] = " "
    return "".join(output)


def _parse_maven_xml(text: str, path: Path) -> tuple[ET.Element, dict[int, int], str]:
    searchable = _mask_xml_non_elements(text)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", searchable, re.IGNORECASE):
        raise ValueError(f"XML document type declarations are not supported: {path}")
    try:
        document = ET.fromstring(text)
    except (ET.ParseError, RecursionError) as exc:
        raise ValueError(f"invalid XML in configured Maven POM: {path}") from exc

    elements: list[ET.Element] = []
    stack: list[tuple[ET.Element, int]] = [(document, 0)]
    while stack:
        element, depth = stack.pop()
        if depth > _MAX_XML_DEPTH:
            raise ValueError(f"Maven POM nesting exceeds {_MAX_XML_DEPTH} levels: {path}")
        elements.append(element)
        if len(elements) > _MAX_XML_NODES:
            raise ValueError(f"Maven POM exceeds {_MAX_XML_NODES} elements: {path}")
        stack.extend((child, depth + 1) for child in reversed(list(element)))

    tag_pattern = re.compile(
        r"<\s*(?![/!?])(?P<name>[^\s/>]+)"
    )
    tokens = [
        (_xml_local_name(match.group("name")), match.start())
        for match in tag_pattern.finditer(searchable)
    ]
    if len(tokens) != len(elements) or any(
        name != _xml_local_name(element.tag)
        for (name, _), element in zip(tokens, elements, strict=True)
    ):
        raise ValueError(f"Maven POM source locations could not be resolved safely: {path}")
    offsets = {
        id(element): offset
        for (_, offset), element in zip(tokens, elements, strict=True)
    }
    return document, offsets, searchable


def _xml_declaration_major(element: ET.Element) -> str | None:
    if list(element):
        return None
    return _major_from_literal(element.text or "")


def _xml_value_offset(text: str, searchable: str, opening_offset: int) -> int:
    quote: str | None = None
    closing = opening_offset
    while closing < len(searchable):
        character = searchable[closing]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character == ">":
            break
        closing += 1
    if closing >= len(searchable):
        return opening_offset
    offset = closing + 1
    while offset < len(text) and text[offset].isspace():
        offset += 1
    cdata_marker = "<![CDATA["
    if text.startswith(cdata_marker, offset):
        offset += len(cdata_marker)
        while offset < len(text) and text[offset].isspace():
            offset += 1
    return offset


def _scan_maven(root: Path, path: Path, budget: ScanBudget) -> list[_Declaration]:
    text = _read_scan_text(root, path, budget)
    document, offsets, searchable = _parse_maven_xml(text, path)
    candidates: list[tuple[int, ET.Element]] = []
    if _xml_local_name(document.tag) == "properties":
        property_groups = (document,)
    else:
        property_groups = tuple(
            child for child in document if _xml_local_name(child.tag) == "properties"
        )
    for properties in property_groups:
        for child in properties:
            if _xml_local_name(child.tag) in {
                "java.version",
                "maven.compiler.release",
                "maven.compiler.source",
                "maven.compiler.target",
            }:
                candidates.append((offsets[id(child)], child))

    parents = {id(child): parent for parent in document.iter() for child in parent}

    def in_profile(element: ET.Element) -> bool:
        parent = parents.get(id(element))
        while parent is not None:
            if _xml_local_name(parent.tag) == "profile":
                return True
            parent = parents.get(id(parent))
        return False

    for plugin in document.iter():
        if _xml_local_name(plugin.tag) != "plugin":
            continue
        if in_profile(plugin):
            continue
        children = list(plugin)
        artifact_ids = [
            child
            for child in children
            if _xml_local_name(child.tag) == "artifactId"
            and not list(child)
            and (child.text or "").strip() == "maven-compiler-plugin"
        ]
        if not artifact_ids:
            continue
        for configuration in plugin.iter():
            if _xml_local_name(configuration.tag) != "configuration":
                continue
            for child in configuration:
                if _xml_local_name(child.tag) in {"release", "source", "target"}:
                    candidates.append((offsets[id(child)], child))

    declarations: list[_Declaration] = []
    locator = _OffsetLocator(root, path, text)
    for opening_offset, element in sorted(candidates, key=lambda item: item[0]):
        value_offset = _xml_value_offset(text, searchable, opening_offset)
        _append_declaration(
            declarations,
            _Declaration(
                source="maven",
                location=locator.location(value_offset),
                major=_xml_declaration_major(element),
            ),
            path,
        )
    return declarations


def _scan_java_version_file(
    root: Path,
    path: Path,
    cache: dict[Path, tuple[Location, str | None]],
    budget: ScanBudget,
) -> list[_Declaration]:
    relative = path.relative_to(root)
    cached = cache.get(relative)
    if cached is not None:
        location, major = cached
        return [_Declaration("version_files", location, major)]
    text = _read_scan_text(root, path, budget)
    for line_number, line in enumerate(io.StringIO(text), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        column = line.find(stripped) + 1
        location = _location(root, path, line_number, max(column, 1))
        major = parse_java_version_file_major(stripped)
        cache[relative] = (location, major)
        return [_Declaration(source="version_files", location=location, major=major)]
    location = _location(root, path, 1)
    cache[relative] = (location, None)
    return [_Declaration("version_files", location, None)]


def _substitute_literal_args(value: str, arguments: Mapping[str, str]) -> str:
    if len(value) > _MAX_DOCKER_IMAGE_CHARS:
        raise ValueError(
            f"Docker image reference exceeds {_MAX_DOCKER_IMAGE_CHARS} characters"
        )

    parts: list[str] = []
    cursor = 0
    length = 0
    for match in _ARG_REFERENCE_RE.finditer(value):
        name = match.group("braced") or match.group("plain")
        literal = value[cursor : match.start()]
        replacement = arguments.get(name, match.group(0))
        length += len(literal) + len(replacement)
        if length > _MAX_DOCKER_IMAGE_CHARS:
            raise ValueError(
                f"Docker image expansion exceeds {_MAX_DOCKER_IMAGE_CHARS} characters"
            )
        parts.extend((literal, replacement))
        cursor = match.end()

    suffix = value[cursor:]
    if length + len(suffix) > _MAX_DOCKER_IMAGE_CHARS:
        raise ValueError(
            f"Docker image expansion exceeds {_MAX_DOCKER_IMAGE_CHARS} characters"
        )
    parts.append(suffix)
    return "".join(parts)


def _image_repository_and_tag(image: str) -> tuple[str, str | None]:
    without_digest = image.partition("@")[0]
    slash = without_digest.rfind("/")
    colon = without_digest.rfind(":")
    if colon > slash:
        repository = without_digest[:colon]
        tag = without_digest[colon + 1 :]
    else:
        repository = without_digest
        tag = None
    return repository.rstrip("/").rsplit("/", 1)[-1].lower(), tag


def _image_java_major(image: str) -> tuple[bool, str | None]:
    repository, tag = _image_repository_and_tag(image)
    known_java = repository in _KNOWN_JAVA_IMAGES or repository.startswith(
        ("liberica-openjdk-", "zulu-openjdk-")
    )
    known_build = repository in _KNOWN_BUILD_IMAGES
    if not known_java and not known_build:
        return False, None
    if tag is None or "$" in tag:
        return True, None

    legacy = _LEGACY_VERSION_RE.fullmatch(tag)
    if legacy is not None:
        return True, legacy.group("major")
    embedded = _EMBEDDED_JAVA_TAG_RE.search(tag)
    if embedded is not None:
        return True, embedded.group("major")
    if known_build:
        return True, None
    leading = _LEADING_IMAGE_VERSION_RE.match(tag)
    return True, leading.group("major") if leading is not None else None


def _scan_dockerfile(root: Path, path: Path, budget: ScanBudget) -> list[_Declaration]:
    text = _read_scan_text(root, path, budget)
    arguments: dict[str, str] = {}
    declarations: list[_Declaration] = []
    seen_from = False
    for line_number, line in enumerate(io.StringIO(text), start=1):
        content = line.rstrip("\r\n")
        if not content.lstrip().startswith("#"):
            argument = _DOCKER_ARG_RE.match(content)
            if argument is not None and not seen_from:
                value = (argument.group("value") or "").strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                if value and "$" not in value:
                    arguments[argument.group("name")] = value
                else:
                    arguments.pop(argument.group("name"), None)
            image_match = _DOCKER_FROM_RE.match(content)
            if image_match is not None:
                seen_from = True
                image = _substitute_literal_args(image_match.group("image"), arguments)
                known, major = _image_java_major(image)
                if known:
                    _append_declaration(
                        declarations,
                        _Declaration(
                            source="dockerfiles",
                            location=_location(
                                root,
                                path,
                                line_number,
                                image_match.start("image") + 1,
                            ),
                            major=major,
                        ),
                        path,
                    )
    return declarations


def _scan_compose(root: Path, path: Path, budget: ScanBudget) -> list[_Declaration]:
    text = _read_scan_text(root, path, budget)
    declarations: list[_Declaration] = []
    for document in _yaml_documents(text, path):
        mappings = _MappingResolver()
        services = mappings.value(document, "services")
        if not isinstance(services, MappingNode):
            continue
        for _, service in services.value:
            for image in mappings.values(service, "image"):
                if not isinstance(image, ScalarNode):
                    continue
                known, major = _image_java_major(image.value.strip())
                if known:
                    _append_declaration(
                        declarations,
                        _Declaration(
                            source="compose",
                            location=_node_location(root, path, image),
                            major=major,
                        ),
                        path,
                    )
    return declarations


def _safe_version_file(root: Path, value: str) -> Path | None:
    normalized = value.replace("\\", "/").removeprefix("./")
    pure_path = PurePosixPath(normalized)
    if (
        not normalized
        or pure_path.is_absolute()
        or ".." in pure_path.parts
        or (len(normalized) >= 3 and normalized[1:3] == ":/")
    ):
        raise ValueError("setup-java version file must stay inside the repository")
    candidate = root / Path(*pure_path.parts)
    if not candidate.exists() and not candidate.is_symlink():
        return None
    # Validate containment now, but preserve the lexical path so ``read_limited_text`` can reject
    # a symlink in any parent component instead of silently reading its resolved target.
    contained_path(root, candidate, label="setup-java version file")
    return candidate


def _declaration_from_version_file(
    root: Path,
    workflow_path: Path,
    node: ScalarNode,
    result: ScanResult,
    cache: dict[Path, tuple[Location, str | None]],
    budget: ScanBudget,
    diagnostics: SourceDiagnostics | None = None,
) -> _Declaration:
    value = node.value.strip()
    if not value or "${{" in value or "$" in value:
        return _Declaration("workflows", _node_location(root, workflow_path, node), None)
    version_path = _safe_version_file(root, value)
    if version_path is None:
        return _Declaration("workflows", _node_location(root, workflow_path, node), None)

    relative = version_path.relative_to(root)
    cached = cache.get(relative)
    if cached is not None:
        location, major = cached
        return _Declaration("workflows", location, major)
    result.scanned_files.add(relative)
    if diagnostics is not None:
        diagnostics.record_derived(relative)
    if len(result.scanned_files) > MAX_SCAN_FILES:
        raise ValueError(f"version file discovery exceeds {MAX_SCAN_FILES} files")
    text = _read_scan_text(root, version_path, budget)
    candidates: list[tuple[int, str, int]] = []
    for line_number, line in enumerate(io.StringIO(text), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if version_path.name == ".tool-versions":
            parts = stripped.split(maxsplit=2)
            if len(parts) < 2 or parts[0] != "java":
                continue
            candidate = parts[1]
        else:
            candidate = stripped.split(maxsplit=1)[0]
        candidates.append((line_number, candidate, line.find(candidate) + 1))
        break
    if not candidates:
        location = _location(root, version_path, 1)
        cache[relative] = (location, None)
        return _Declaration("workflows", location, None)
    line_number, candidate, column = candidates[0]
    location = _location(root, version_path, line_number, max(column, 1))
    major = parse_java_version_file_major(candidate)
    cache[relative] = (location, major)
    return _Declaration("workflows", location, major)


def _scan_workflow(
    root: Path,
    path: Path,
    result: ScanResult,
    version_file_cache: dict[Path, tuple[Location, str | None]],
    budget: ScanBudget,
    diagnostics: SourceDiagnostics | None = None,
) -> list[_Declaration]:
    text = _read_scan_text(root, path, budget)
    declarations: list[_Declaration] = []
    for document in _yaml_documents(text, path):
        mappings = _MappingResolver()
        jobs = mappings.value(document, "jobs")
        if not isinstance(jobs, MappingNode):
            continue
        for _, job in jobs.value:
            steps = mappings.value(job, "steps")
            if not isinstance(steps, SequenceNode):
                continue
            for step in steps.value:
                uses = mappings.value(step, "uses")
                if not isinstance(uses, ScalarNode) or not uses.value.strip().lower().startswith(
                    "actions/setup-java@"
                ):
                    continue
                inputs = mappings.value(step, "with")
                if not isinstance(inputs, MappingNode):
                    continue
                java_version = mappings.value(inputs, "java-version")
                if java_version is not None:
                    if isinstance(java_version, ScalarNode):
                        _append_declaration(
                            declarations,
                            _Declaration(
                                "workflows",
                                _node_location(root, path, java_version),
                                _major_from_literal(java_version.value),
                            ),
                            path,
                        )
                    else:
                        _append_declaration(
                            declarations,
                            _Declaration(
                                "workflows", _node_location(root, path, java_version), None
                            ),
                            path,
                        )
                    continue
                version_file = mappings.value(inputs, "java-version-file")
                if isinstance(version_file, ScalarNode):
                    _append_declaration(
                        declarations,
                        _declaration_from_version_file(
                            root,
                            path,
                            version_file,
                            result,
                            version_file_cache,
                            budget,
                            diagnostics,
                        ),
                        path,
                    )
                elif version_file is not None:
                    _append_declaration(
                        declarations,
                        _Declaration(
                            "workflows", _node_location(root, path, version_file), None
                        ),
                        path,
                    )
    return declarations


def _doc_value_major(value: str) -> str | None:
    if _DOC_RANGE_RE.search(value):
        return None
    majors = {
        major
        for match in _DOC_VERSION_TOKEN_RE.finditer(value)
        if (major := _major_from_literal(match.group(0))) is not None
    }
    return next(iter(majors)) if len(majors) == 1 else None


def _without_html_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    """Replace Markdown HTML comments with spaces while preserving source columns."""

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


def _scan_document(root: Path, path: Path, budget: ScanBudget) -> list[_Declaration]:
    text = _read_scan_text(root, path, budget)
    declarations: list[_Declaration] = []
    fence: tuple[str, int] | None = None
    in_comment = False
    for line_number, line in enumerate(io.StringIO(text), start=1):
        content = line.rstrip("\r\n")
        if fence is not None:
            fence_match = _MARKDOWN_FENCE_RE.match(content)
            if fence_match is not None:
                token = fence_match.group("fence")
                remainder = content[fence_match.end() :]
                if (
                    token[0] == fence[0]
                    and len(token) >= fence[1]
                    and not remainder.strip()
                ):
                    fence = None
            continue

        visible, in_comment = _without_html_comments(content, in_comment)
        fence_match = _MARKDOWN_FENCE_RE.match(visible)
        if fence_match is not None:
            token = fence_match.group("fence")
            fence = (token[0], len(token))
            continue
        match = _DOC_TABLE_RE.match(visible) or _DOC_DECLARATION_RE.match(visible)
        if match is not None:
            value = match.group("value")
            normalized = value.strip().strip("`*_ ").lower()
            if normalized not in {"version", "major", "---", ":---", "---:"}:
                _append_declaration(
                    declarations,
                    _Declaration(
                        source="docs",
                        location=_location(
                            root,
                            path,
                            line_number,
                            match.start("value") + 1,
                        ),
                        major=_doc_value_major(value),
                    ),
                    path,
                )
    return declarations


def _matches_any(value: str, patterns: Sequence[str]) -> bool:
    normalized = value.removeprefix("./")
    return any(
        fnmatch.fnmatchcase(normalized, pattern.removeprefix("./"))
        or Path(normalized).match(pattern.removeprefix("./"))
        for pattern in patterns
    )


def _patterns(section: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = section.get(key, VERSION_JAVA_DEFAULTS.get(key, []))
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"versions.java.{key} must be a list of strings")
    if not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"versions.java.{key} must be a list of strings")
    return tuple(value)


def _java_config(config: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if "versions" not in config:
        return None
    versions = config["versions"]
    if not isinstance(versions, Mapping) or not isinstance(versions.get("java"), Mapping):
        raise ValueError("versions.java must be a mapping")
    return versions["java"]


def _declaration_key(declaration: _Declaration) -> tuple[str, str, int, int, str]:
    location = declaration.location
    return (
        declaration.source,
        location.path.as_posix(),
        location.line,
        location.column,
        declaration.major or "",
    )


def _deduplicate(declarations: Iterable[_Declaration]) -> list[_Declaration]:
    unique = {_declaration_key(item): item for item in declarations}
    return [unique[key] for key in sorted(unique)]


def _group_locations(
    declarations: Iterable[_Declaration],
) -> list[tuple[tuple[str, str, str], list[Location]]]:
    groups: dict[tuple[str, str, str], dict[tuple[int, int], Location]] = {}
    for declaration in declarations:
        key = (
            declaration.source,
            declaration.location.path.as_posix(),
            declaration.major or "dynamic",
        )
        groups.setdefault(key, {})[
            (declaration.location.line, declaration.location.column)
        ] = declaration.location
    return [
        (key, [locations[item] for item in sorted(locations)])
        for key, locations in sorted(groups.items())
    ]


def _without_duplicate_workflow_version_files(
    declarations: Sequence[_Declaration],
) -> list[_Declaration]:
    """Report a shared setup-java version file once while preserving source presence."""

    direct = {
        (
            item.location.path,
            item.location.line,
            item.location.column,
            item.major,
        )
        for item in declarations
        if item.source == "version_files"
    }
    return [
        item
        for item in declarations
        if item.source != "workflows"
        or (
            item.location.path,
            item.location.line,
            item.location.column,
            item.major,
        )
        not in direct
    ]


def scan_version_contracts(
    root: Path,
    config: Mapping[str, Any],
    *,
    diagnostics: ScannerDiagnostics | None = None,
) -> ScanResult:
    """Scan an explicitly configured Java major-version contract below ``root``.

    The optional ``versions.java`` mapping supplies ``expected`` and repository-relative
    patterns for Gradle, Maven, ``.java-version``, Dockerfiles, Compose, GitHub Actions
    workflows, and documentation. ``required`` names source groups that must contain at least one
    declaration. Dynamic declarations are reported without retaining or displaying their values.
    """

    section = _java_config(config)
    if section is None:
        return ScanResult()
    expected = section.get("expected")
    if not isinstance(expected, str) or not _JAVA_MAJOR_RE.fullmatch(expected):
        raise ValueError("versions.java.expected must be a canonical Java major from 1 to 999")

    root = Path(root).resolve(strict=True)
    ignored = _patterns(section, "ignore")
    required = _patterns(section, "required")
    if len(required) != len(set(required)) or any(item not in _SOURCE_NAMES for item in required):
        raise ValueError("versions.java.required contains an invalid source name")

    result = ScanResult()
    declarations: list[_Declaration] = []
    version_file_cache: dict[Path, tuple[Location, str | None]] = {}
    first_candidate: dict[str, Path] = {}
    budget = ScanBudget("version", "declarations", _VERSION_SCAN_LIMITS)
    scanners = {
        "gradle": lambda path, source_diagnostics: _scan_gradle(root, path, budget),
        "maven": lambda path, source_diagnostics: _scan_maven(root, path, budget),
        "version_files": lambda path, source_diagnostics: _scan_java_version_file(
            root, path, version_file_cache, budget
        ),
        "dockerfiles": lambda path, source_diagnostics: _scan_dockerfile(root, path, budget),
        "compose": lambda path, source_diagnostics: _scan_compose(root, path, budget),
        "workflows": lambda path, source_diagnostics: _scan_workflow(
            root,
            path,
            result,
            version_file_cache,
            budget,
            source_diagnostics,
        ),
        "docs": lambda path, source_diagnostics: _scan_document(root, path, budget),
    }
    for source in _SOURCE_NAMES:
        patterns = _patterns(section, source)
        source_diagnostics = (
            diagnostics.source(source, patterns, required=source in required)
            if diagnostics is not None
            else None
        )
        for path in discover_files(root, patterns, diagnostics=source_diagnostics):
            relative = path.relative_to(root)
            if _matches_any(relative.as_posix(), ignored):
                if source_diagnostics is not None:
                    source_diagnostics.record_ignored(relative, "configured_ignore")
                continue
            first_candidate.setdefault(source, relative)
            result.scanned_files.add(relative)
            if len(result.scanned_files) > MAX_SCAN_FILES:
                raise ValueError(f"version file discovery exceeds {MAX_SCAN_FILES} files")
            discovered = scanners[source](path, source_diagnostics)
            if len(discovered) > _MAX_DECLARATIONS_PER_FILE:
                raise ValueError(
                    f"version declarations exceed {_MAX_DECLARATIONS_PER_FILE} in one file"
                )
            if len(declarations) + len(discovered) > _MAX_TOTAL_DECLARATIONS:
                raise ValueError(
                    f"version declarations exceed {_MAX_TOTAL_DECLARATIONS} in one scan"
                )
            declarations.extend(discovered)

    declarations = _deduplicate(declarations)
    present_sources = {declaration.source for declaration in declarations}
    reported_declarations = _without_duplicate_workflow_version_files(declarations)
    for (source, relative_path, observed), locations in _group_locations(reported_declarations):
        label = _SOURCE_LABELS[source]
        if observed == "dynamic":
            result.findings.append(
                Finding(
                    code="VER002",
                    message=(
                        f"Java version in {label} is dynamic and cannot be compared with "
                        f"contract '{expected}'."
                    ),
                    severity=Severity.WARNING,
                    location=locations[0],
                    hint="Replace the dynamic value with a literal Java major where practical.",
                    related=tuple(locations[1:]),
                    baseline_key=f"java:dynamic:{source}:{relative_path}",
                )
            )
        elif observed != expected:
            result.findings.append(
                Finding(
                    code="VER001",
                    message=(
                        f"Java version in {label} is '{observed}', but the contract expects "
                        f"'{expected}'."
                    ),
                    severity=Severity.ERROR,
                    location=locations[0],
                    hint=f"Align this declaration with Java {expected}.",
                    related=tuple(locations[1:]),
                    baseline_key=f"java:mismatch:{source}:{relative_path}:{observed}",
                )
            )

    for source in sorted(set(required) - present_sources):
        label = _SOURCE_LABELS[source]
        result.findings.append(
            Finding(
                code="VER003",
                message=f"Required Java version source '{label}' has no declaration.",
                severity=Severity.WARNING,
                location=(
                    Location(first_candidate[source], 1, 1)
                    if source in first_candidate
                    else None
                ),
                hint=f"Add a literal Java {expected} declaration to the configured {label} files.",
                baseline_key=f"java:required:{source}",
            )
        )

    return apply_rule_policy(result, config)


__all__ = ["parse_java_version_file_major", "scan_version_contracts"]
