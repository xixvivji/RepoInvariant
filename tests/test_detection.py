from pathlib import Path

import yaml

from repoinvariant.cli import main
from repoinvariant.config import load_config
from repoinvariant.detection import detect_config
from repoinvariant.version_contracts import (
    parse_java_version_file_major,
    scan_version_contracts,
)


def _write(root: Path, relative: str, text: str = "synthetic\n") -> None:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def test_detection_and_scanner_share_java_version_file_parsing(tmp_path: Path) -> None:
    cases = (
        ("openjdk64-21.0.1", "21"),
        ("graalvm64-17.0.8", "17"),
        ("graalvm-ce-java17-22.3.0", "17"),
        ("temurin-21.0.2+13", "21"),
        ("temurin-17.0.12+7", "17"),
    )
    for index, (value, expected) in enumerate(cases):
        root = tmp_path / str(index)
        root.mkdir()
        _write(root, ".java-version", f"{value}\n")

        config = detect_config(root)

        assert config["versions"]["java"]["expected"] == expected
        assert scan_version_contracts(root, config).findings == []

    assert parse_java_version_file_major("temurin-21-or-17") is None


def test_init_detect_writes_only_matching_convention_ranges_deterministically(
    tmp_path: Path, capsys
) -> None:
    _write(tmp_path, ".env.example", "DECLARED=\n")
    _write(tmp_path, "compose.yaml", "services: {}\n")
    _write(tmp_path, "k8s/deployment.yaml", "apiVersion: v1\nkind: ConfigMap\n")
    _write(tmp_path, ".github/workflows/ci.yml", "jobs: {}\n")
    _write(tmp_path, "src/main/resources/application.properties", "key=value\n")
    _write(tmp_path, "docs/requirements.md", "# REQ-DETECT\n")
    _write(tmp_path, "openapi.yml", "openapi: 3.1.0\n")
    _write(tmp_path, "tests/test_api.py", "# REQ-DETECT\n")
    _write(tmp_path, ".java-version", "temurin-21.0.2+13\n")
    _write(tmp_path, "build.gradle", "java { toolchain {} }\n")
    _write(tmp_path, "package.json", "{}\n")

    assert main(["init", str(tmp_path), "--detect"]) == 0
    assert capsys.readouterr().err == ""
    generated = (tmp_path / ".repoinvariant.yml").read_text(encoding="utf-8")
    raw = yaml.safe_load(generated)

    assert raw["env"] == {
        "contracts": [".env.example"],
        "compose": ["compose*.yaml"],
        "kubernetes": ["k8s/**/*.yaml"],
        "workflows": [".github/workflows/*.yml"],
        "spring": ["src/main/resources/application*.properties"],
        "ignore": [
            "CI",
            "HOME",
            "PATH",
            "PWD",
            "SHELL",
            "USER",
            "GH_TOKEN",
            "GITHUB_*",
            "RUNNER_*",
        ],
    }
    assert raw["features"] == {
        "requirements": ["docs/**/*.md"],
        "specifications": ["openapi*.yml"],
        "tests": ["tests/**/*"],
        "id_pattern": r"\bREQ-[A-Z0-9][A-Z0-9-]*\b",
        "openapi_extension": "x-feature-id",
        "requirements_mode": "definitions",
        "ignore": [],
    }
    assert raw["versions"] == {
        "java": {
            "expected": "21",
            "gradle": ["**/build.gradle"],
            "maven": [],
            "version_files": ["**/.java-version"],
            "dockerfiles": [],
            "compose": ["**/compose*.yaml"],
            "workflows": [".github/workflows/*.yml"],
            "docs": ["docs/**/*.md"],
            "ignore": [],
            "required": [],
        }
    }
    assert set(raw["rules"]) == {
        "ENV001",
        "ENV002",
        "ENV003",
        "TRACE001",
        "TRACE002",
        "TRACE003",
        "TRACE004",
        "VER001",
        "VER002",
        "VER003",
    }
    assert "package.json" not in generated
    assert load_config(tmp_path)["versions"]["java"]["expected"] == "21"

    assert main(["init", str(tmp_path), "--detect", "--force"]) == 0
    capsys.readouterr()
    assert (tmp_path / ".repoinvariant.yml").read_text(encoding="utf-8") == generated


def test_init_detect_turns_off_rule_families_without_artifacts(
    tmp_path: Path, capsys
) -> None:
    _write(tmp_path, "compose.yml", "services: {}\n")

    assert main(["init", str(tmp_path), "--detect"]) == 0
    raw = yaml.safe_load(
        (tmp_path / ".repoinvariant.yml").read_text(encoding="utf-8")
    )
    capsys.readouterr()

    assert raw["env"]["compose"] == ["compose*.yml"]
    assert all(not raw["features"][name] for name in ("requirements", "specifications", "tests"))
    assert {raw["rules"][code] for code in ("TRACE001", "TRACE002", "TRACE003", "TRACE004")} == {
        "off"
    }
    assert main(["doctor", str(tmp_path), "--strict"]) == 0
    assert capsys.readouterr().err == ""


def test_init_detect_refuses_empty_or_unsafe_discovery(
    tmp_path: Path, capsys
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main(["init", str(empty), "--detect"]) == 2
    captured = capsys.readouterr()
    assert "no supported repository artifacts" in captured.err
    assert not (empty / ".repoinvariant.yml").exists()

    outside = tmp_path / "outside.env"
    outside.write_text("PRIVATE=synthetic\n", encoding="utf-8")
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    try:
        (unsafe / ".env.example").symlink_to(outside)
    except OSError:
        return

    assert main(["init", str(unsafe), "--detect"]) == 2
    captured = capsys.readouterr()
    assert "symbolic link" in captured.err
    assert "synthetic" not in captured.err
    assert not (unsafe / ".repoinvariant.yml").exists()
