from __future__ import annotations

import os
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


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO files are POSIX-specific")
def test_read_rejects_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    fifo = tmp_path / "version.pipe"
    os.mkfifo(fifo)

    with pytest.raises(ValueError, match="not a regular file"):
        read_limited_text(fifo, root=tmp_path)


def test_atomic_create_preserves_file_created_during_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    destination = root / "baseline.json"
    real_link = os.link

    def race_link(source: str, target: str, **kwargs: object) -> None:
        destination.write_text("competing writer\n", encoding="utf-8")
        real_link(source, target, **kwargs)

    monkeypatch.setattr("repoinvariant.filesystem.os.link", race_link)

    with pytest.raises(ValueError, match="already exists"):
        atomic_write_text(
            root,
            destination,
            "baseline contents\n",
            label="baseline output",
            overwrite=False,
        )

    assert destination.read_text(encoding="utf-8") == "competing writer\n"
