from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.version import Version

from repotruth import __version__


def test_package_version_has_one_source_of_truth() -> None:
    root = Path(__file__).parents[1]
    configuration = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert "version" in configuration["project"]["dynamic"]
    assert "version" not in configuration["project"]
    assert configuration["tool"]["hatch"]["version"]["path"] == "src/repotruth/__init__.py"
    assert str(Version(__version__)) == __version__
