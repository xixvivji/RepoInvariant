"""Cross-check environment-variable contracts against common configuration consumers."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

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

_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOTENV_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*(?P<equals>=)\s*(?P<value>.*))?\s*$"
)
_COMPOSE_REFERENCE_RE = re.compile(
    r"(?<!\$)\$(?:\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:(?P<operator>:-|-|:\?|\?|:\+|\+)(?P<argument>[^}]*))?\}"
    r"|(?P<plain_name>[A-Za-z_][A-Za-z0-9_]*))"
)
_PLAIN_REFERENCE_RE = re.compile(r"(?<!\$)\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")
_GITHUB_REFERENCE_RE = re.compile(r"\b(?:secrets|vars)\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")
_SPRING_REFERENCE_RE = re.compile(
    r"(?<!\$)\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?::(?P<default>[^{}]*))?\}"
)
_SPRING_PROPERTY_ASSIGNMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:=|:)\s*(?P<value>.*)$"
)
_MAX_YAML_DEPTH = 100
_MAX_YAML_NODES = 20_000
_ENV_SCAN_LIMITS = ScanLimits(
    max_files=MAX_SCAN_FILES,
    max_input_bytes=64 * 1024 * 1024,
    max_items=100_000,
    max_findings=10_000,
    max_related_locations=100,
    max_report_bytes=8 * 1024 * 1024,
)

_DEFAULT_PATTERNS: dict[str, tuple[str, ...]] = {
    "contracts": (".env", ".env.example", ".env.sample", ".env.template"),
    "compose": (
        "compose.yml",
        "compose.yaml",
        "compose.*.yml",
        "compose.*.yaml",
        "docker-compose.yml",
        "docker-compose.yaml",
        "docker-compose.*.yml",
        "docker-compose.*.yaml",
    ),
    "kubernetes": (
        "k8s/**/*.yml",
        "k8s/**/*.yaml",
        "kubernetes/**/*.yml",
        "kubernetes/**/*.yaml",
    ),
    "workflows": (".github/workflows/*.yml", ".github/workflows/*.yaml"),
    "spring": (
        "**/application*.yml",
        "**/application*.yaml",
        "**/application*.properties",
        "**/bootstrap*.yml",
        "**/bootstrap*.yaml",
        "**/bootstrap*.properties",
    ),
}


@dataclass(frozen=True, slots=True)
class _Occurrence:
    name: str
    location: Location
    default_digest: str | None = None


def _default_digest(value: str) -> str:
    """Return an opaque comparison token so configuration values never reach findings."""

    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _strip_dotenv_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            continue
        if character == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _normalize_dotenv_value(value: str) -> str:
    value = _strip_dotenv_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


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


def _relative_path(root: Path, path: Path) -> Path | None:
    try:
        safe_path = contained_path(root, path, label="configured file")
        return safe_path.relative_to(root.resolve())
    except ValueError:
        return None


def _location(root: Path, path: Path, line: int, column: int = 1) -> Location:
    relative = _relative_path(root, path)
    return Location(path=relative if relative is not None else path, line=line, column=column)


def _append_occurrence(
    occurrences: list[_Occurrence], occurrence: _Occurrence, budget: ScanBudget
) -> None:
    budget.record_item()
    occurrences.append(occurrence)


def _parse_dotenv(root: Path, path: Path, budget: ScanBudget) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    text = _read_scan_text(root, path, budget)
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        match = _DOTENV_LINE_RE.match(raw_line)
        if match is None:
            continue
        name = match.group("name")
        value = match.group("value")
        digest = None
        if match.group("equals") is not None:
            raw_value = _strip_dotenv_comment(value or "").strip()
            if raw_value:
                digest = _default_digest(_normalize_dotenv_value(value or ""))
        column = raw_line.find(name) + 1
        _append_occurrence(
            occurrences,
            _Occurrence(name, _location(root, path, line_number, max(column, 1)), digest),
            budget,
        )
    return occurrences


def _yaml_documents(text: str, path: Path) -> list[Node]:
    try:
        documents: list[Node] = []
        node_budget = [0]
        for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
            if document is None:
                node_budget[0] += 1
                if node_budget[0] > _MAX_YAML_NODES:
                    raise ValueError(f"YAML input exceeds {_MAX_YAML_NODES} nodes")
                continue
            for _ in _walk_nodes(document, node_budget=node_budget):
                pass
            documents.append(document)
        return documents
    except (RecursionError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid YAML in configured file: {path}") from exc


@dataclass(slots=True)
class _MappingResolver:
    """Resolve bounded YAML merge mappings without constructing repository values."""

    cache: dict[tuple[int, str], Node | None] = field(default_factory=dict)
    active: set[tuple[int, str]] = field(default_factory=set)

    def value(self, node: Node, name: str) -> Node | None:
        if not isinstance(node, MappingNode):
            return None
        key = (id(node), name)
        if key in self.cache:
            return self.cache[key]
        if key in self.active:
            return None
        self.active.add(key)
        try:
            direct = [
                value_node
                for key_node, value_node in node.value
                if isinstance(key_node, ScalarNode) and key_node.value == name
            ]
            resolved = direct[0] if direct else None
            if resolved is None:
                for key_node, value_node in node.value:
                    if not isinstance(key_node, ScalarNode) or key_node.value != "<<":
                        continue
                    candidates: Sequence[Node]
                    if isinstance(value_node, SequenceNode):
                        candidates = value_node.value
                    else:
                        candidates = (value_node,)
                    for candidate in candidates:
                        resolved = self.value(candidate, name)
                        if resolved is not None:
                            break
                    if resolved is not None:
                        break
            self.cache[key] = resolved
            return resolved
        finally:
            self.active.remove(key)


def _walk_nodes(node: Node, *, node_budget: list[int] | None = None) -> Iterable[Node]:
    seen: set[int] = set()
    active: set[int] = set()
    budget = node_budget if node_budget is not None else [0]

    def walk(current: Node, depth: int) -> Iterable[Node]:
        if depth > _MAX_YAML_DEPTH:
            raise ValueError(f"YAML nesting exceeds {_MAX_YAML_DEPTH} levels")
        identity = id(current)
        if identity in active:
            raise ValueError("YAML alias cycle is not supported")
        if identity in seen:
            return

        seen.add(identity)
        budget[0] += 1
        if budget[0] > _MAX_YAML_NODES:
            raise ValueError(f"YAML input exceeds {_MAX_YAML_NODES} nodes")
        active.add(identity)
        try:
            yield current
            if isinstance(current, MappingNode):
                scalar_keys: set[tuple[str, str]] = set()
                for key_node, value_node in current.value:
                    if isinstance(key_node, ScalarNode):
                        key = (key_node.tag, key_node.value)
                        if key in scalar_keys:
                            raise ValueError("duplicate YAML mapping key is not supported")
                        scalar_keys.add(key)
                    yield from walk(key_node, depth + 1)
                    yield from walk(value_node, depth + 1)
            elif isinstance(current, SequenceNode):
                for child in current.value:
                    yield from walk(child, depth + 1)
        finally:
            active.remove(identity)

    yield from walk(node, 0)


def _node_location(root: Path, path: Path, node: Node, offset: int = 0) -> Location:
    line = node.start_mark.line + 1
    column = node.start_mark.column + 1
    if isinstance(node, ScalarNode) and offset:
        prefix = node.value[:offset]
        if "\n" in prefix:
            line += prefix.count("\n")
            column = len(prefix.rsplit("\n", 1)[-1]) + 1
        else:
            column += offset
    return _location(root, path, line, column)


def _literal_yaml_default(
    node: Node | None, dynamic_patterns: Sequence[re.Pattern[str]]
) -> str | None:
    if not isinstance(node, ScalarNode) or node.tag == "tag:yaml.org,2002:null":
        return None
    if any(pattern.search(node.value) for pattern in dynamic_patterns):
        return None
    return _default_digest(node.value)


def _compose_references(
    root: Path, path: Path, documents: Iterable[Node], budget: ScanBudget
) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    for document in documents:
        for node in _walk_nodes(document):
            if not isinstance(node, ScalarNode) or node.style == "'":
                continue
            for match in _COMPOSE_REFERENCE_RE.finditer(node.value):
                digest = None
                if match.group("operator") in {":-", "-"}:
                    digest = _default_digest(match.group("argument") or "")
                _append_occurrence(
                    occurrences,
                    _Occurrence(
                        match.group("name") or match.group("plain_name"),
                        _node_location(root, path, node, match.start()),
                        digest,
                    ),
                    budget,
                )
    return occurrences


def _compose_environment(
    root: Path, path: Path, documents: Iterable[Node], budget: ScanBudget
) -> tuple[list[_Occurrence], list[ScalarNode]]:
    occurrences: list[_Occurrence] = []
    env_files: list[ScalarNode] = []
    for document in documents:
        mappings = _MappingResolver()
        services = mappings.value(document, "services")
        if not isinstance(services, MappingNode):
            continue
        for _, service in services.value:
            if not isinstance(service, MappingNode):
                continue
            environment = mappings.value(service, "environment")
            if isinstance(environment, MappingNode):
                for key_node, value_node in environment.value:
                    if not isinstance(key_node, ScalarNode) or not _ENV_NAME_RE.fullmatch(
                        key_node.value
                    ):
                        continue
                    if isinstance(value_node, ScalarNode) and (
                        value_node.tag == "tag:yaml.org,2002:null"
                    ):
                        _append_occurrence(
                            occurrences,
                            _Occurrence(
                                key_node.value,
                                _node_location(root, path, key_node),
                            ),
                            budget,
                        )
            elif isinstance(environment, SequenceNode):
                for item in environment.value:
                    if not isinstance(item, ScalarNode):
                        continue
                    name, separator, _ = item.value.partition("=")
                    name = name.strip()
                    if not _ENV_NAME_RE.fullmatch(name):
                        continue
                    if not separator:
                        _append_occurrence(
                            occurrences,
                            _Occurrence(name, _node_location(root, path, item)),
                            budget,
                        )

            env_file = mappings.value(service, "env_file")
            candidates: list[Node] = []
            if isinstance(env_file, ScalarNode):
                candidates.append(env_file)
            elif isinstance(env_file, SequenceNode):
                candidates.extend(env_file.value)
            for candidate in candidates:
                if isinstance(candidate, MappingNode):
                    candidate = mappings.value(candidate, "path") or candidate
                if isinstance(candidate, ScalarNode):
                    env_files.append(candidate)
    return occurrences, env_files


def _scan_compose(
    root: Path,
    path: Path,
    result: ScanResult,
    ignored_paths: Sequence[str],
    budget: ScanBudget,
    diagnostics: SourceDiagnostics | None = None,
) -> list[_Occurrence]:
    text = _read_scan_text(root, path, budget)
    documents = _yaml_documents(text, path)
    occurrences = _compose_references(root, path, documents, budget)
    environment, env_file_nodes = _compose_environment(root, path, documents, budget)
    occurrences.extend(environment)
    for env_file_node in env_file_nodes:
        reference = env_file_node.value.strip()
        if not reference or _COMPOSE_REFERENCE_RE.search(reference):
            continue
        try:
            candidate = contained_path(
                root,
                path.parent / reference,
                label="Compose env_file",
            )
        except ValueError as exc:
            raise ValueError("Compose env_file must stay inside the repository") from exc
        relative = candidate.relative_to(root.resolve())
        if not candidate.is_file():
            continue
        if _matches_any(relative.as_posix(), ignored_paths):
            if diagnostics is not None:
                diagnostics.record_ignored(relative, "configured_ignore")
            continue
        budget.record_file(relative)
        result.scanned_files.add(relative)
        if diagnostics is not None:
            diagnostics.record_derived(relative)
        occurrences.extend(_parse_dotenv(root, candidate, budget))
    return occurrences


def _container_environment(
    root: Path, path: Path, document: Node, budget: ScanBudget
) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    mappings = _MappingResolver()
    for node in _walk_nodes(document):
        if not isinstance(node, MappingNode):
            continue
        for key_node, value_node in node.value:
            if (
                not isinstance(key_node, ScalarNode)
                or key_node.value not in {"containers", "initContainers"}
                or not isinstance(value_node, SequenceNode)
            ):
                continue
            for container in value_node.value:
                environment = mappings.value(container, "env")
                if not isinstance(environment, SequenceNode):
                    continue
                for entry in environment.value:
                    name_node = mappings.value(entry, "name")
                    if not isinstance(name_node, ScalarNode) or not _ENV_NAME_RE.fullmatch(
                        name_node.value
                    ):
                        continue
                    value = mappings.value(entry, "value")
                    digest = _literal_yaml_default(value, (_PLAIN_REFERENCE_RE,))
                    _append_occurrence(
                        occurrences,
                        _Occurrence(
                            name_node.value,
                            _node_location(root, path, name_node),
                            digest,
                        ),
                        budget,
                    )
    return occurrences


def _scan_kubernetes(root: Path, path: Path, budget: ScanBudget) -> list[_Occurrence]:
    text = _read_scan_text(root, path, budget)
    documents = _yaml_documents(text, path)
    occurrences: list[_Occurrence] = []
    for document in documents:
        occurrences.extend(_container_environment(root, path, document, budget))
        for node in _walk_nodes(document):
            if not isinstance(node, ScalarNode):
                continue
            for match in _PLAIN_REFERENCE_RE.finditer(node.value):
                _append_occurrence(
                    occurrences,
                    _Occurrence(
                        match.group("name"),
                        _node_location(root, path, node, match.start()),
                    ),
                    budget,
                )
    return occurrences


def _scan_workflow(root: Path, path: Path, budget: ScanBudget) -> list[_Occurrence]:
    text = _read_scan_text(root, path, budget)
    documents = _yaml_documents(text, path)
    occurrences: list[_Occurrence] = []
    for document in documents:
        for node in _walk_nodes(document):
            if isinstance(node, ScalarNode):
                for match in _GITHUB_REFERENCE_RE.finditer(node.value):
                    _append_occurrence(
                        occurrences,
                        _Occurrence(
                            match.group("name"),
                            _node_location(root, path, node, match.start()),
                        ),
                        budget,
                    )
    return occurrences


def _spring_references_in_node(
    root: Path, path: Path, document: Node, budget: ScanBudget
) -> list[_Occurrence]:
    occurrences: list[_Occurrence] = []
    static_properties: set[str] = set()
    if isinstance(document, MappingNode):
        for key_node, value_node in document.value:
            if (
                isinstance(key_node, ScalarNode)
                and _ENV_NAME_RE.fullmatch(key_node.value)
                and isinstance(value_node, ScalarNode)
                and value_node.tag != "tag:yaml.org,2002:null"
                and _SPRING_REFERENCE_RE.search(value_node.value) is None
            ):
                static_properties.add(key_node.value)
    for node in _walk_nodes(document):
        if not isinstance(node, ScalarNode):
            continue
        for match in _SPRING_REFERENCE_RE.finditer(node.value):
            default = match.group("default")
            _append_occurrence(
                occurrences,
                _Occurrence(
                    match.group("name"),
                    _node_location(root, path, node, match.start()),
                    _default_digest(default) if default is not None else None,
                ),
                budget,
            )
    return [item for item in occurrences if item.name not in static_properties]


def _scan_spring(root: Path, path: Path, budget: ScanBudget) -> list[_Occurrence]:
    text = _read_scan_text(root, path, budget)
    if path.suffix.lower() != ".properties":
        documents = _yaml_documents(text, path)
        if documents:
            return [
                occurrence
                for document in documents
                for occurrence in _spring_references_in_node(root, path, document, budget)
            ]

    occurrences: list[_Occurrence] = []
    property_is_static: dict[str, bool] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith(("#", "!")):
            continue
        assignment = _SPRING_PROPERTY_ASSIGNMENT_RE.match(line)
        if assignment is not None:
            property_is_static[assignment.group("name")] = (
                _SPRING_REFERENCE_RE.search(assignment.group("value")) is None
            )
        for match in _SPRING_REFERENCE_RE.finditer(line):
            default = match.group("default")
            _append_occurrence(
                occurrences,
                _Occurrence(
                    match.group("name"),
                    _location(root, path, line_number, match.start() + 1),
                    _default_digest(default) if default is not None else None,
                ),
                budget,
            )
    return [
        item
        for item in occurrences
        if property_is_static.get(item.name) is not True
    ]


def _as_patterns(value: Any, fallback: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return fallback
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(item for item in value if isinstance(item, str) and item)
    return fallback


def _env_config(config_mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = config_mapping.get("env")
    return nested if isinstance(nested, Mapping) else config_mapping


def _config_patterns(
    config_mapping: Mapping[str, Any], section: Mapping[str, Any], key: str
) -> tuple[str, ...]:
    dotted_key = f"env.{key}"
    if dotted_key in config_mapping:
        return _as_patterns(config_mapping[dotted_key])
    if key in section:
        return _as_patterns(section[key])
    return _DEFAULT_PATTERNS.get(key, ())


def _matches_any(value: str, patterns: Sequence[str]) -> bool:
    normalized = value.removeprefix("./")
    return any(
        fnmatch.fnmatchcase(normalized, pattern.removeprefix("./"))
        or Path(normalized).match(pattern.removeprefix("./"))
        for pattern in patterns
    )


def _expand_files(
    root: Path,
    patterns: Sequence[str],
    ignored: Sequence[str],
    diagnostics: SourceDiagnostics | None = None,
) -> list[Path]:
    files: dict[str, Path] = {}
    resolved_root = root.resolve()
    for pattern in patterns:
        if Path(pattern).is_absolute():
            candidates = [Path(pattern)]
        else:
            try:
                candidates = root.glob(pattern)
            except (OSError, ValueError):
                continue
        for candidate in candidates:
            try:
                lexical_relative = candidate.relative_to(resolved_root)
            except ValueError:
                lexical_relative = None
            if lexical_relative is not None and _matches_any(
                lexical_relative.as_posix(), ignored
            ):
                if diagnostics is not None:
                    try:
                        safe_candidate = contained_path(root, candidate, label="configured file")
                    except ValueError as exc:
                        raise ValueError(
                            "configured environment file path is unsafe"
                        ) from exc
                    if safe_candidate.is_file():
                        diagnostics.record_ignored(
                            safe_candidate.relative_to(resolved_root),
                            "configured_ignore",
                        )
                continue
            if candidate.is_symlink():
                raise ValueError(f"configured file must not be a symbolic link: {candidate}")
            try:
                safe_candidate = contained_path(root, candidate, label="configured file")
            except ValueError as exc:
                raise ValueError("configured environment file path is unsafe") from exc
            if not safe_candidate.is_file():
                continue
            relative = safe_candidate.relative_to(resolved_root)
            files[relative.as_posix()] = safe_candidate
            if diagnostics is not None:
                diagnostics.record_matched(relative)
            if len(files) > MAX_SCAN_FILES:
                raise ValueError(f"environment file discovery exceeds {MAX_SCAN_FILES} files")
    return [files[key] for key in sorted(files)]


def _occurrence_key(occurrence: _Occurrence) -> tuple[str, str, int, int, str]:
    location = occurrence.location
    return (
        occurrence.name,
        location.path.as_posix(),
        location.line,
        location.column,
        occurrence.default_digest or "",
    )


def _deduplicate(occurrences: Iterable[_Occurrence]) -> list[_Occurrence]:
    unique: dict[tuple[str, str, int, int, str], _Occurrence] = {}
    for occurrence in occurrences:
        unique[_occurrence_key(occurrence)] = occurrence
    return [unique[key] for key in sorted(unique)]


def _locations(occurrences: Iterable[_Occurrence]) -> list[Location]:
    unique = {
        (item.location.path.as_posix(), item.location.line, item.location.column): item.location
        for item in occurrences
    }
    return [unique[key] for key in sorted(unique)]


def _bounded_evidence(
    locations: Sequence[Location], hint: str, budget: ScanBudget
) -> tuple[tuple[Location, ...], str]:
    related, omitted = budget.bound_related(locations[1:])
    if omitted:
        hint = (
            f"{hint} Showing the first {len(related)} related locations; "
            f"{omitted} additional locations were omitted by the scan limit."
        )
    return related, hint


def _append_finding(result: ScanResult, finding: Finding, budget: ScanBudget) -> None:
    payload = json.dumps(
        finding.as_dict(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    budget.record_finding(len(payload))
    result.findings.append(finding)


def scan_env_contracts(
    root: Path,
    config_mapping: Mapping[str, Any],
    *,
    diagnostics: ScannerDiagnostics | None = None,
) -> ScanResult:
    """Scan configured environment contracts and consumers below ``root``.

    ``config_mapping`` accepts either an ``env`` mapping or that mapping directly. The
    supported list keys are ``contracts``, ``compose``, ``kubernetes``, ``workflows``,
    ``spring``, and ``ignore``. File entries are repository-relative glob patterns;
    ignore entries can match either a path or an environment-variable name.
    """

    root = Path(root).resolve()
    section = _env_config(config_mapping)
    ignored = _config_patterns(config_mapping, section, "ignore")
    result = ScanResult()
    budget = ScanBudget("environment", "occurrences", _ENV_SCAN_LIMITS)

    contracts: list[_Occurrence] = []
    consumers: list[_Occurrence] = []

    contract_patterns = _config_patterns(config_mapping, section, "contracts")
    contract_diagnostics = (
        diagnostics.source("contracts", contract_patterns) if diagnostics is not None else None
    )
    contract_paths = _expand_files(root, contract_patterns, ignored, contract_diagnostics)
    for path in contract_paths:
        relative = _relative_path(root, path)
        if relative is not None:
            budget.record_file(relative)
            result.scanned_files.add(relative)
        contracts.extend(_parse_dotenv(root, path, budget))

    scanners = {
        "compose": lambda path, source_diagnostics: _scan_compose(
            root,
            path,
            result,
            ignored,
            budget,
            source_diagnostics,
        ),
        "kubernetes": lambda path: _scan_kubernetes(root, path, budget),
        "workflows": lambda path: _scan_workflow(root, path, budget),
        "spring": lambda path: _scan_spring(root, path, budget),
    }
    for scanner_name, scanner in scanners.items():
        patterns = _config_patterns(config_mapping, section, scanner_name)
        source_diagnostics = (
            diagnostics.source(scanner_name, patterns) if diagnostics is not None else None
        )
        paths = _expand_files(root, patterns, ignored, source_diagnostics)
        for path in paths:
            relative = _relative_path(root, path)
            if relative is not None:
                budget.record_file(relative)
                result.scanned_files.add(relative)
            if scanner_name == "compose":
                consumers.extend(scanner(path, source_diagnostics))
            else:
                consumers.extend(scanner(path))

    contracts = _deduplicate(
        occurrence for occurrence in contracts if not _matches_any(occurrence.name, ignored)
    )
    consumers = _deduplicate(
        occurrence for occurrence in consumers if not _matches_any(occurrence.name, ignored)
    )

    contracts_by_name: dict[str, list[_Occurrence]] = {}
    consumers_by_name: dict[str, list[_Occurrence]] = {}
    for occurrence in contracts:
        contracts_by_name.setdefault(occurrence.name, []).append(occurrence)
    for occurrence in consumers:
        consumers_by_name.setdefault(occurrence.name, []).append(occurrence)

    for name in sorted(consumers_by_name.keys() - contracts_by_name.keys()):
        locations = _locations(consumers_by_name[name])
        hint = "Declare the variable in an environment contract or explicitly ignore it."
        related, hint = _bounded_evidence(locations, hint, budget)
        _append_finding(
            result,
            Finding(
                code="ENV001",
                message=f"Environment variable '{name}' is consumed but missing from the contract.",
                severity=Severity.ERROR,
                location=locations[0],
                hint=hint,
                related=related,
                baseline_key=name,
            ),
            budget,
        )

    for name in sorted(contracts_by_name.keys() - consumers_by_name.keys()):
        locations = _locations(contracts_by_name[name])
        hint = "Remove the stale declaration or add a matching consumer."
        related, hint = _bounded_evidence(locations, hint, budget)
        _append_finding(
            result,
            Finding(
                code="ENV002",
                message=f"Contract variable '{name}' is not consumed.",
                severity=Severity.WARNING,
                location=locations[0],
                hint=hint,
                related=related,
                baseline_key=name,
            ),
            budget,
        )

    all_by_name: dict[str, list[_Occurrence]] = {}
    for occurrence in chain(contracts, consumers):
        all_by_name.setdefault(occurrence.name, []).append(occurrence)
    for name in sorted(all_by_name):
        explicit = [item for item in all_by_name[name] if item.default_digest is not None]
        if len({item.default_digest for item in explicit}) <= 1:
            continue
        locations = _locations(explicit)
        hint = "Choose one default across the contract and all consumers."
        related, hint = _bounded_evidence(locations, hint, budget)
        _append_finding(
            result,
            Finding(
                code="ENV003",
                message=f"Environment variable '{name}' has conflicting explicit defaults.",
                severity=Severity.WARNING,
                location=locations[0],
                hint=hint,
                related=related,
                baseline_key=name,
            ),
            budget,
        )

    return apply_rule_policy(result, config_mapping)


__all__ = ["scan_env_contracts"]
