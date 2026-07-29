from __future__ import annotations

from pathlib import Path
from textwrap import indent

import pytest

from repotruth.env_contracts import scan_env_contracts
from repotruth.filesystem import MAX_SCAN_BYTES


def _config(section: str, filename: str) -> dict[str, list[str]]:
    config = {
        "contracts": [],
        "compose": [],
        "kubernetes": [],
        "workflows": [],
        "spring": [],
    }
    config[section] = [filename]
    return config


@pytest.mark.parametrize(
    ("section", "filename"),
    [
        ("compose", "compose.yml"),
        ("kubernetes", "deployment.yml"),
        ("workflows", "workflow.yml"),
        ("spring", "application.yml"),
    ],
)
def test_malformed_yaml_is_rejected(tmp_path: Path, section: str, filename: str) -> None:
    (tmp_path / filename).write_text("broken: [\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid YAML"):
        scan_env_contracts(tmp_path, _config(section, filename))


def test_yaml_alias_cycle_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "compose.yml").write_text(
        """cycle: &cycle
  self: *cycle
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="alias cycle"):
        scan_env_contracts(tmp_path, _config("compose", "compose.yml"))


def test_yaml_nesting_depth_is_bounded(tmp_path: Path) -> None:
    document = "leaf: value\n"
    for level in range(110):
        document = f"level_{level}:\n{indent(document, '  ')}"
    (tmp_path / "compose.yml").write_text(document, encoding="utf-8")

    with pytest.raises(ValueError, match="nesting exceeds"):
        scan_env_contracts(tmp_path, _config("compose", "compose.yml"))


def test_non_cyclic_yaml_alias_is_scanned_once(tmp_path: Path) -> None:
    (tmp_path / "compose.yml").write_text(
        """shared: &shared ${SHARED_INPUT}
first: *shared
second: *shared
""",
        encoding="utf-8",
    )

    result = scan_env_contracts(tmp_path, _config("compose", "compose.yml"))

    assert [finding.code for finding in result.findings] == ["ENV001"]
    assert "'SHARED_INPUT'" in result.findings[0].message


def test_scan_file_size_is_bounded(tmp_path: Path) -> None:
    (tmp_path / "compose.yml").write_bytes(b"#" + (b"x" * MAX_SCAN_BYTES))

    with pytest.raises(ValueError, match=f"exceeds {MAX_SCAN_BYTES} bytes"):
        scan_env_contracts(tmp_path, _config("compose", "compose.yml"))


def test_configured_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real-compose.yml"
    target.write_text("value: ${SHOULD_NOT_BE_SCANNED}\n", encoding="utf-8")
    configured = tmp_path / "compose.yml"
    try:
        configured.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    with pytest.raises(ValueError, match="symbolic link"):
        scan_env_contracts(tmp_path, _config("compose", configured.name))


def test_compose_env_file_outside_repository_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (tmp_path / "outside.env").write_text("OUTSIDE=value\n", encoding="utf-8")
    (root / "compose.yml").write_text(
        """services:
  app:
    env_file: ../outside.env
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must stay inside the repository"):
        scan_env_contracts(root, _config("compose", "compose.yml"))


def test_invalid_utf8_yaml_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "compose.yml").write_bytes(b"services:\n  bad: \xff\n")

    with pytest.raises(ValueError, match="invalid UTF-8"):
        scan_env_contracts(tmp_path, _config("compose", "compose.yml"))
