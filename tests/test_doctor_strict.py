import json
from pathlib import Path

from repoinvariant.cli import main


def _write(root: Path, relative: str, text: str) -> None:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def test_doctor_strict_fails_for_an_effective_empty_scanner(
    tmp_path: Path, capsys
) -> None:
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: []
  compose: []
  kubernetes: []
  workflows: []
  spring: []
  ignore: []
features:
  requirements: []
  specifications: []
  tests: []
  ignore: []
rules:
  TRACE001: off
  TRACE002: off
  TRACE003: off
  TRACE004: off
""",
    )

    assert main(["doctor", str(tmp_path), "--strict", "--format", "json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["summary"]["unique_scanned_files"] == 0
    assert "scanner 'env' is active but scanned no files" in captured.err
    assert "scanner 'features'" not in captured.err

    assert (
        main(
            [
                "doctor",
                str(tmp_path),
                "--strict",
                "--no-env",
                "--format",
                "json",
            ]
        )
        == 0
    )
    assert capsys.readouterr().err == ""


def test_doctor_strict_fails_when_a_required_range_has_no_declaration(
    tmp_path: Path, capsys
) -> None:
    _write(tmp_path, ".env.example", "DECLARED=\n")
    _write(tmp_path, "README.md", "Java: 21\n")
    _write(tmp_path, "build.gradle.kts", "plugins {}\n")
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [.env.example]
  compose: []
  kubernetes: []
  workflows: []
  spring: []
  ignore: []
features:
  requirements: []
  specifications: []
  tests: []
  ignore: []
versions:
  java:
    expected: "21"
    gradle: [build.gradle.kts]
    dockerfiles: []
    compose: []
    workflows: []
    docs: [README.md]
    ignore: []
    required: [gradle]
rules:
  TRACE001: off
  TRACE002: off
  TRACE003: off
  TRACE004: off
  VER001: off
  VER002: off
  VER003: off
""",
    )

    assert main(["doctor", str(tmp_path), "--strict"]) == 1
    captured = capsys.readouterr()
    assert "gradle: matched; required" in captured.out
    assert "required source 'gradle' has no recognized declaration (matched)" in captured.err
    assert "scanned no files" not in captured.err


def test_doctor_strict_accepts_a_populated_required_range(
    tmp_path: Path, capsys
) -> None:
    _write(tmp_path, ".env.example", "DECLARED=\n")
    _write(tmp_path, "build.gradle.kts", "kotlin { jvmToolchain(21) }\n")
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [.env.example]
  compose: []
  kubernetes: []
  workflows: []
  spring: []
  ignore: []
features:
  requirements: []
  specifications: []
  tests: []
  ignore: []
versions:
  java:
    expected: "21"
    gradle: [build.gradle.kts]
    dockerfiles: []
    compose: []
    workflows: []
    docs: []
    ignore: []
    required: [gradle]
rules:
  TRACE001: off
  TRACE002: off
  TRACE003: off
  TRACE004: off
""",
    )

    assert main(["doctor", str(tmp_path), "--strict"]) == 0
    assert capsys.readouterr().err == ""
