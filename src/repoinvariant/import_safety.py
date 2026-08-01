"""Narrow import-path isolation for the CLI and explicitly selected plugins."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType


class UnsafeImportPathError(ValueError):
    """Raised when a local target can be imported only through a broad search root."""


def _resolved_path(value: object) -> Path | None:
    if not isinstance(value, (str, os.PathLike)):
        return None
    try:
        candidate = Path.cwd() if os.fspath(value) == "" else Path(value)
        return candidate.expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _deduplicated_paths(values: Iterable[Path | None]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def unsafe_import_roots(
    *,
    repository_root: Path | None = None,
    include_working_directory: bool = False,
) -> tuple[Path, ...]:
    """Return resolved repository, working-directory, and ``PYTHONPATH`` roots."""

    values: list[Path | None] = []
    if repository_root is not None:
        values.append(_resolved_path(repository_root))
    if include_working_directory:
        values.append(_resolved_path(Path.cwd()))
    pythonpath = os.environ.get("PYTHONPATH")
    if pythonpath is not None:
        values.extend(_resolved_path(item) for item in pythonpath.split(os.pathsep))
    return _deduplicated_paths(values)


def _is_within(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _is_runtime_site_path(path: Path) -> bool:
    if not ({"site-packages", "dist-packages"} & set(path.parts)):
        return False
    prefixes = _deduplicated_paths(
        _resolved_path(value)
        for value in (sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix)
    )
    return _is_within(path, prefixes)


def sanitize_import_path(
    *,
    repository_root: Path | None = None,
    include_working_directory: bool = False,
) -> tuple[Path, ...]:
    """Remove untrusted local roots from ``sys.path`` and return those roots.

    The mutation is process-local and deliberate: selected in-process plugins may import lazily
    during scanning, so restoring repository paths immediately after entry-point loading would
    reopen the same module-shadowing boundary.
    """

    roots = unsafe_import_roots(
        repository_root=repository_root,
        include_working_directory=include_working_directory,
    )
    if not roots:
        return ()
    retained: list[str] = []
    for entry in sys.path:
        resolved = _resolved_path(entry)
        if (
            resolved is not None
            and _is_within(resolved, roots)
            and not _is_runtime_site_path(resolved)
        ):
            continue
        retained.append(entry)
    sys.path[:] = retained
    importlib.invalidate_caches()
    return roots


def _target_candidates(base: Path, module_name: str) -> tuple[Path, ...]:
    parts = module_name.split(".")
    candidates: list[Path] = []
    for index in range(1, len(parts) + 1):
        package = base.joinpath(*parts[:index])
        candidates.extend((package, package.with_suffix(".py")))
    return tuple(candidates)


def _candidate_uses_roots(candidate: Path, roots: tuple[Path, ...]) -> bool:
    try:
        if not candidate.exists():
            return False
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False
    return _is_within(resolved, roots)


def sanitize_target_import_path(module_name: str, roots: Iterable[Path]) -> None:
    """Remove search bases that would resolve a selected target from local roots.

    An exact repository/PYTHONPATH base is removed by :func:`sanitize_import_path`.  This
    target-aware pass also handles a repository package importable through its direct parent.
    Broader ancestors such as the filesystem root are not removed because doing so could break
    unrelated runtime imports; the selected plugin instead fails closed before its loader runs.
    """

    parts = module_name.split(".")
    if not parts or any(not part.isidentifier() for part in parts):
        raise UnsafeImportPathError("invalid target module")
    root_tuple = tuple(roots)
    retained: list[str] = []
    changed = False
    for entry in sys.path:
        base = _resolved_path(entry)
        if base is None or not any(
            _candidate_uses_roots(candidate, root_tuple)
            for candidate in _target_candidates(base, module_name)
        ):
            retained.append(entry)
            continue
        if any(root.parent == base for root in root_tuple):
            changed = True
            continue
        raise UnsafeImportPathError("target module is visible through a broad import root")
    if changed:
        sys.path[:] = retained
        importlib.invalidate_caches()


def _module_paths(module: ModuleType) -> tuple[Path, ...]:
    values: list[object] = []
    namespace = vars(module)
    values.append(namespace.get("__file__"))
    spec = namespace.get("__spec__")
    if spec is not None:
        origin = getattr(spec, "origin", None)
        if origin not in {"built-in", "frozen", "namespace"}:
            values.append(origin)
        locations = getattr(spec, "submodule_search_locations", None)
        if locations is not None:
            values.extend(locations)
    package_paths = namespace.get("__path__")
    if package_paths is not None:
        values.extend(package_paths)
    return _deduplicated_paths(_resolved_path(value) for value in values)


def loaded_module_uses_roots(module_name: str, roots: Iterable[Path]) -> bool:
    """Return whether a loaded target module or parent package came from local roots."""

    root_tuple = tuple(roots)
    retained_roots = tuple(
        path
        for path in (_resolved_path(entry) for entry in sys.path)
        if path is not None
        and path not in root_tuple
        and any(_is_within(path, (root,)) for root in root_tuple)
        and _is_runtime_site_path(path)
    )
    parts = module_name.split(".")
    for index in range(1, len(parts) + 1):
        module = sys.modules.get(".".join(parts[:index]))
        if not isinstance(module, ModuleType):
            continue
        if any(
            _is_within(path, root_tuple)
            and not any(_is_within(path, (retained_root,)) for retained_root in retained_roots)
            for path in _module_paths(module)
        ):
            return True
    return False


__all__ = [
    "UnsafeImportPathError",
    "loaded_module_uses_roots",
    "sanitize_import_path",
    "sanitize_target_import_path",
    "unsafe_import_roots",
]
