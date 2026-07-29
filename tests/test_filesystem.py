from __future__ import annotations

from pathlib import Path

import pytest

from repoinvariant.filesystem import atomic_write_text, read_limited_text


def test_descriptor_based_read_and_atomic_write(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("before\n", encoding="utf-8")

    assert read_limited_text(source, root=root) == "before\n"
    assert atomic_write_text(root, source, "after\n", label="output") == source
    assert source.read_text(encoding="utf-8") == "after\n"


def test_parent_directory_symlink_cannot_redirect_read_or_write(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    outside_file = outside / "report.txt"
    outside_file.write_text("outside\n", encoding="utf-8")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic links"):
        read_limited_text(root / "linked" / "report.txt", root=root)
    with pytest.raises(ValueError, match="symbolic links"):
        atomic_write_text(root, root / "linked" / "report.txt", "changed\n", label="output")

    assert outside_file.read_text(encoding="utf-8") == "outside\n"
