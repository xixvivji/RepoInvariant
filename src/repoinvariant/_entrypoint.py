"""Safe console-script bootstrap that imports the full CLI after path isolation."""

from __future__ import annotations

import sys as _sys


def _bootstrap_path(value: object, *, working_directory: str, os_module: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        candidate = working_directory if value == "" else value
        return os_module.path.normcase(os_module.path.realpath(candidate))
    except (OSError, RuntimeError, ValueError):
        return None


def _bootstrap_within(path: str, root: str, *, os_module: object) -> bool:
    try:
        return os_module.path.commonpath((path, root)) == root
    except (OSError, RuntimeError, ValueError):
        return False


def _bootstrap_runtime_site_path(
    path: str, *, working_directory: str, os_module: object
) -> bool:
    parts = set(path.split(os_module.sep))
    if not ({"site-packages", "dist-packages"} & parts):
        return False
    prefixes = {
        _bootstrap_path(value, working_directory=working_directory, os_module=os_module)
        for value in (
            _sys.prefix,
            _sys.exec_prefix,
            _sys.base_prefix,
            _sys.base_exec_prefix,
        )
    }
    return any(
        prefix is not None and _bootstrap_within(path, prefix, os_module=os_module)
        for prefix in prefixes
    )


def _sanitize_bootstrap_import_path() -> None:
    """Use only startup-loaded modules until local import roots have been removed."""

    os_module = _sys.modules.get("os")
    if os_module is None:
        raise RuntimeError("RepoInvariant requires Python's normal site initialization")
    working_directory = os_module.getcwd()
    roots = {
        _bootstrap_path(
            working_directory,
            working_directory=working_directory,
            os_module=os_module,
        )
    }
    pythonpath = os_module.environ.get("PYTHONPATH")
    if pythonpath is not None:
        roots.update(
            _bootstrap_path(item, working_directory=working_directory, os_module=os_module)
            for item in pythonpath.split(os_module.pathsep)
        )
    retained = []
    for entry in _sys.path:
        resolved = _bootstrap_path(
            entry,
            working_directory=working_directory,
            os_module=os_module,
        )
        if resolved is not None and any(
            root is not None and _bootstrap_within(resolved, root, os_module=os_module)
            for root in roots
        ) and not _bootstrap_runtime_site_path(
            resolved,
            working_directory=working_directory,
            os_module=os_module,
        ):
            continue
        retained.append(entry)
    _sys.path[:] = retained


_sanitize_bootstrap_import_path()


def main(argv=None) -> int:
    """Run the CLI without importing dependencies from the working directory or ``PYTHONPATH``."""

    from repoinvariant.cli import main as cli_main

    return cli_main(argv)


__all__ = ["main"]
