"""Bounded, repository-contained filesystem operations."""

from __future__ import annotations

import errno
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path

MAX_CONFIG_BYTES = 256 * 1024
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_SCAN_FILES = 10_000

_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _repository_relative(root: Path, path: Path, *, label: str) -> tuple[Path, Path]:
    resolved_root = root.resolve(strict=True)
    candidate = path if path.is_absolute() else resolved_root / path
    lexical = Path(os.path.abspath(os.fspath(candidate)))
    try:
        relative = lexical.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside repository root: {candidate}") from exc
    if not relative.parts:
        raise ValueError(f"{label} must name a file inside repository root: {candidate}")
    return resolved_root, relative


def _open_parent(root: Path, relative: Path, *, label: str) -> int:
    """Open the parent directory without following any path-component symlink."""

    descriptor = os.open(root, _DIRECTORY_FLAGS | _NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(
                component,
                _DIRECTORY_FLAGS | _NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
    except OSError as exc:
        os.close(descriptor)
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"{label} path must not contain symbolic links") from exc
        raise
    return descriptor


def contained_path(
    root: Path,
    path: Path,
    *,
    label: str,
    must_exist: bool = True,
    reject_symlink: bool = True,
) -> Path:
    """Return a resolved path only when it stays inside ``root``."""

    resolved_root = root.resolve()
    candidate = path if path.is_absolute() else resolved_root / path
    if reject_symlink and candidate.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link: {candidate}")
    if must_exist and not candidate.exists():
        raise ValueError(f"{label} does not exist: {candidate}")

    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} must stay inside repository root: {candidate}") from exc
    return resolved


def read_limited_text(
    path: Path,
    *,
    root: Path | None = None,
    max_bytes: int = MAX_SCAN_BYTES,
    errors: str = "strict",
) -> str:
    """Read a regular file through a no-follow descriptor with a hard byte limit."""

    safe_root = root if root is not None else path.parent
    resolved_root, relative = _repository_relative(safe_root, path, label="configured file")
    parent_descriptor = _open_parent(resolved_root, relative, label="configured file")
    file_descriptor: int | None = None
    try:
        try:
            file_descriptor = os.open(
                relative.name,
                os.O_RDONLY | _NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(f"configured file must not be a symbolic link: {path}") from exc
            raise
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"configured path is not a regular file: {path}")
        if metadata.st_size > max_bytes:
            raise ValueError(f"configured file exceeds {max_bytes} bytes: {path}")

        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(file_descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            raise ValueError(f"configured file exceeds {max_bytes} bytes: {path}")
        return data.decode("utf-8-sig", errors=errors)
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        os.close(parent_descriptor)


def atomic_write_text(
    root: Path,
    destination: Path,
    text: str,
    *,
    label: str,
    overwrite: bool = True,
) -> Path:
    """Atomically create or replace a file through a verified parent descriptor."""

    encoded = text.encode("utf-8")
    resolved_root, relative = _repository_relative(root, destination, label=label)
    parent_descriptor = _open_parent(resolved_root, relative, label=label)
    temporary_name: str | None = None
    temporary_descriptor: int | None = None
    try:
        try:
            existing = os.stat(relative.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not overwrite:
                raise ValueError(f"{label} already exists: {destination}")
            if stat.S_ISLNK(existing.st_mode):
                raise ValueError(f"{label} must not be a symbolic link: {destination}")
            if not stat.S_ISREG(existing.st_mode):
                raise ValueError(f"{label} must be a regular file: {destination}")

        for _ in range(32):
            temporary_name = f".{relative.name}.{secrets.token_hex(8)}.tmp"
            try:
                temporary_descriptor = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                    0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileExistsError:
                temporary_name = None
        if temporary_descriptor is None or temporary_name is None:
            raise OSError("could not allocate a temporary output file")

        view = memoryview(encoded)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise OSError("could not write temporary output file")
            view = view[written:]
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        if overwrite:
            os.replace(
                temporary_name,
                relative.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        else:
            try:
                os.link(
                    temporary_name,
                    relative.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ValueError(f"{label} already exists: {destination}") from exc
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        temporary_name = None
        os.fsync(parent_descriptor)
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if temporary_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.close(parent_descriptor)
    return resolved_root / relative
