import json
import os
from pathlib import Path

import pytest

from repoinvariant.cli import main


def _write(root: Path, relative: str, text: str) -> None:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def _doctor_json(
    root: Path,
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> tuple[int, dict[str, object], str]:
    exit_code = main(["doctor", str(root), "--format", "json", *arguments])
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out else {}
    return exit_code, payload, captured.err


def _by_name(items: object) -> dict[str, dict[str, object]]:
    assert isinstance(items, list)
    assert all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in items)
    return {item["name"]: item for item in items}


def _by_code(items: object) -> dict[str, dict[str, object]]:
    assert isinstance(items, list)
    assert all(isinstance(item, dict) and isinstance(item.get("code"), str) for item in items)
    return {item["code"]: item for item in items}


def _assert_hidden_path_collection(value: object, *, count: int) -> None:
    assert value == {"count": count, "omitted_count": count, "paths": []}


def _assert_hidden_pattern_collection(value: object, *, count: int) -> None:
    assert value == {"count": count, "omitted_count": count, "values": []}


def _write_full_diagnostic_fixture(root: Path) -> None:
    _write(root, ".env.example", "DECLARED=\n")
    _write(
        root,
        "compose.yml",
        "services:\n  api:\n    env_file:\n      - runtime.env\n",
    )
    _write(root, "runtime.env", "RUNTIME_ONLY=synthetic\n")
    _write(root, "ignored/application.properties", "ignored=${IGNORED}\n")
    _write(root, "docs/requirements.md", "# REQ-DOCTOR\n\nSynthetic requirement.\n")
    _write(root, "ignored/java.md", "Use Java 21.\n")
    _write(root, ".java-version", "21\n")
    _write(
        root,
        ".github/workflows/ci.yml",
        """jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version-file: .java-version
""",
    )
    _write(
        root,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [.env.example]
  compose: [compose.yml]
  kubernetes: []
  workflows: [missing-workflows/*.yml]
  spring: [ignored/*.properties]
  ignore: [ignored/**]
features:
  requirements: [docs/requirements.md]
  specifications: []
  tests: [missing-tests/**/*]
  id_pattern: '\\bREQ-[A-Z0-9][A-Z0-9-]*\\b'
  openapi_extension: x-feature-id
  ignore: []
versions:
  java:
    expected: "21"
    gradle: []
    dockerfiles: [missing/Dockerfile]
    compose: []
    workflows: [.github/workflows/*.yml]
    docs: [ignored/*.md]
    ignore: [ignored/**]
    required: []
rules:
  TRACE004: off
""",
    )


def test_doctor_json_reports_inventory_without_disclosing_paths_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_full_diagnostic_fixture(tmp_path)

    exit_code = main(["doctor", str(tmp_path), "--format", "json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert set(payload) == {
        "baseline",
        "configuration",
        "repository",
        "rules",
        "scanners",
        "schema_version",
        "summary",
        "tool",
        "verbose",
    }
    assert payload["verbose"]["enabled"] is False
    assert payload["baseline"]["status"] == "not_selected"

    scanners = _by_name(payload["scanners"])
    assert list(scanners) == ["env", "features", "versions"]
    assert {name: scanner["state"] for name, scanner in scanners.items()} == {
        "env": "active",
        "features": "active",
        "versions": "active",
    }
    _assert_hidden_path_collection(scanners["env"]["files"], count=3)
    _assert_hidden_path_collection(scanners["features"]["files"], count=1)
    _assert_hidden_path_collection(scanners["versions"]["files"], count=2)

    env_sources = _by_name(scanners["env"]["sources"])
    assert {name: source["state"] for name, source in env_sources.items()} == {
        "compose": "matched",
        "contracts": "matched",
        "kubernetes": "empty_patterns",
        "spring": "all_ignored",
        "workflows": "no_matches",
    }
    _assert_hidden_pattern_collection(env_sources["compose"]["patterns"], count=1)
    _assert_hidden_path_collection(env_sources["compose"]["matched"], count=1)
    _assert_hidden_path_collection(env_sources["compose"]["derived"], count=1)
    assert env_sources["spring"]["ignored"] == {
        "count": 1,
        "omitted_count": 1,
        "records": [],
    }

    feature_sources = _by_name(scanners["features"]["sources"])
    assert {name: source["state"] for name, source in feature_sources.items()} == {
        "requirements": "matched",
        "specifications": "empty_patterns",
        "tests": "no_matches",
    }

    version_sources = _by_name(scanners["versions"]["sources"])
    assert {name: source["state"] for name, source in version_sources.items()} == {
        "compose": "empty_patterns",
        "dockerfiles": "no_matches",
        "docs": "all_ignored",
        "gradle": "empty_patterns",
        "maven": "no_matches",
        "version_files": "matched",
        "workflows": "matched",
    }
    _assert_hidden_path_collection(version_sources["workflows"]["matched"], count=1)
    _assert_hidden_path_collection(version_sources["workflows"]["derived"], count=0)
    _assert_hidden_path_collection(version_sources["version_files"]["matched"], count=1)

    rules = _by_code(payload["rules"])
    assert rules["TRACE004"]["severity"] == "off"
    assert rules["TRACE004"]["active"] is False
    assert rules["TRACE004"]["inactive_reasons"] == ["rule_off"]

    # The default report exposes counts and states, never matched or derived target paths.
    assert str(tmp_path) not in captured.out
    assert "compose.yml" not in captured.out
    assert "runtime.env" not in captured.out
    assert ".java-version" not in captured.out


def test_doctor_verbose_reports_sorted_matched_and_derived_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_full_diagnostic_fixture(tmp_path)

    exit_code, payload, error = _doctor_json(tmp_path, capsys, "--verbose")

    assert exit_code == 0
    assert error == ""
    assert payload["verbose"]["enabled"] is True
    scanners = _by_name(payload["scanners"])
    assert scanners["env"]["files"] == {
        "count": 3,
        "omitted_count": 0,
        "paths": [".env.example", "compose.yml", "runtime.env"],
    }
    env_sources = _by_name(scanners["env"]["sources"])
    assert env_sources["compose"]["matched"] == {
        "count": 1,
        "omitted_count": 0,
        "paths": ["compose.yml"],
    }
    assert env_sources["compose"]["derived"] == {
        "count": 1,
        "omitted_count": 0,
        "paths": ["runtime.env"],
    }
    assert env_sources["spring"]["ignored"] == {
        "count": 1,
        "omitted_count": 0,
        "records": [
            {"path": "ignored/application.properties", "reason": "configured_ignore"}
        ],
    }

    assert scanners["versions"]["files"] == {
        "count": 2,
        "omitted_count": 0,
        "paths": [".github/workflows/ci.yml", ".java-version"],
    }
    version_sources = _by_name(scanners["versions"]["sources"])
    assert version_sources["workflows"]["matched"] == {
        "count": 1,
        "omitted_count": 0,
        "paths": [".github/workflows/ci.yml"],
    }
    assert version_sources["workflows"]["derived"] == {
        "count": 0,
        "omitted_count": 0,
        "paths": [],
    }
    assert version_sources["version_files"]["matched"] == {
        "count": 1,
        "omitted_count": 0,
        "paths": [".java-version"],
    }
    assert version_sources["docs"]["ignored"] == {
        "count": 1,
        "omitted_count": 0,
        "records": [{"path": "ignored/java.md", "reason": "configured_ignore"}],
    }


def test_doctor_verbose_collections_are_bounded_and_report_omissions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("d.env", "b.env", "c.env", "a.env"):
        _write(tmp_path, f"contracts/{name}", "SYNTHETIC=\n")
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [contracts/*.env]
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
""",
    )
    monkeypatch.setattr("repoinvariant.doctor.PATHS_PER_COLLECTION", 2)

    exit_code, payload, error = _doctor_json(tmp_path, capsys, "--verbose")

    assert exit_code == 0
    assert error == ""
    env = _by_name(payload["scanners"])["env"]
    assert env["files"] == {
        "count": 4,
        "omitted_count": 2,
        "paths": ["contracts/a.env", "contracts/b.env"],
    }
    contracts = _by_name(env["sources"])["contracts"]
    assert contracts["matched"] == {
        "count": 4,
        "omitted_count": 2,
        "paths": ["contracts/a.env", "contracts/b.env"],
    }
    assert contracts["patterns"] == {
        "count": 1,
        "omitted_count": 0,
        "values": ["contracts/*.env"],
    }


def test_doctor_verbose_explains_built_in_and_binary_exclusions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binary = tmp_path / "docs" / "binary.md"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"REQ-BINARY\x00synthetic")
    _write(tmp_path, ".private/check.py", "# REQ-HIDDEN\n")
    _write(tmp_path, "node_modules/example/java.md", "Java: 21\n")
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
  requirements: [docs/**/*]
  specifications: []
  tests: [.private/**/*]
  ignore: []
versions:
  java:
    expected: "21"
    gradle: []
    dockerfiles: []
    compose: []
    workflows: []
    docs: [node_modules/**/*.md]
    ignore: []
    required: []
""",
    )

    exit_code, payload, error = _doctor_json(tmp_path, capsys, "--verbose")

    assert exit_code == 0
    assert error == ""
    features = _by_name(payload["scanners"])["features"]
    sources = _by_name(features["sources"])
    assert sources["requirements"]["state"] == "all_ignored"
    assert sources["requirements"]["ignored"] == {
        "count": 1,
        "omitted_count": 0,
        "records": [{"path": "docs/binary.md", "reason": "binary"}],
    }
    assert sources["tests"]["state"] == "all_ignored"
    assert sources["tests"]["ignored"] == {
        "count": 1,
        "omitted_count": 0,
        "records": [{"path": ".private/check.py", "reason": "built_in_ignore"}],
    }
    version_docs = _by_name(_by_name(payload["scanners"])["versions"]["sources"])["docs"]
    assert version_docs["state"] == "all_ignored"
    assert version_docs["ignored"] == {
        "count": 1,
        "omitted_count": 0,
        "records": [
            {"path": "node_modules/example/java.md", "reason": "built_in_ignore"}
        ],
    }


def test_doctor_distinguishes_not_configured_and_flag_disabled_scanners(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, payload, error = _doctor_json(tmp_path, capsys)

    assert exit_code == 0
    assert error == ""
    scanners = _by_name(payload["scanners"])
    assert scanners["env"]["state"] == "active"
    assert scanners["features"]["state"] == "active"
    assert scanners["versions"]["state"] == "not_configured"
    assert {
        source["state"] for source in _by_name(scanners["versions"]["sources"]).values()
    } == {"not_configured"}

    exit_code, payload, error = _doctor_json(
        tmp_path,
        capsys,
        "--no-env",
        "--no-features",
        "--no-versions",
    )

    assert exit_code == 0
    assert error == ""
    scanners = _by_name(payload["scanners"])
    assert {name: scanner["state"] for name, scanner in scanners.items()} == {
        "env": "disabled_by_flag",
        "features": "disabled_by_flag",
        "versions": "disabled_by_flag",
    }
    for scanner in scanners.values():
        assert {source["state"] for source in _by_name(scanner["sources"]).values()} == {
            "disabled"
        }


def test_doctor_baseline_match_and_scope_mismatch_are_diagnostic_states(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["baseline", str(tmp_path)]) == 0
    capsys.readouterr()

    exit_code, payload, error = _doctor_json(
        tmp_path,
        capsys,
        "--baseline",
        ".repoinvariant-baseline.json",
    )

    assert exit_code == 0
    assert error == ""
    assert payload["baseline"]["status"] == "match"

    exit_code, payload, error = _doctor_json(
        tmp_path,
        capsys,
        "--baseline",
        ".repoinvariant-baseline.json",
        "--no-env",
    )

    assert exit_code == 0
    assert error == ""
    assert payload["baseline"]["status"] == "mismatch"


def test_doctor_invalid_inputs_return_two_without_a_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_root = tmp_path / "missing-root"
    assert main(["doctor", str(missing_root), "--format", "json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "repository root does not exist" in captured.err

    assert main(["doctor", str(tmp_path), "--config", "missing.yml", "--format", "json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "configuration file does not exist" in captured.err

    _write(tmp_path, "invalid-baseline.json", "{}\n")
    assert (
        main(
            [
                "doctor",
                str(tmp_path),
                "--baseline",
                "invalid-baseline.json",
                "--format",
                "json",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "baseline" in captured.err


def test_doctor_invalid_baseline_error_does_not_disclose_fingerprints(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fingerprint = "sha256:" + "a" * 64
    document = {
        "schema_version": 1,
        "fingerprint_version": 1,
        "tool": {"name": "RepoInvariant", "version": "0.3.0"},
        "scope_digest": "sha256:" + "b" * 64,
        "findings": [
            {"code": "ENV001", "severity": "error", "fingerprint": fingerprint},
            {"code": "ENV001", "severity": "error", "fingerprint": fingerprint},
        ],
    }
    _write(tmp_path, "baseline.json", json.dumps(document))

    assert main(["doctor", str(tmp_path), "--baseline", "baseline.json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "selected baseline is invalid or unsafe" in captured.err
    assert fingerprint not in captured.err


def test_doctor_text_reports_states_but_hides_paths_without_verbose(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_full_diagnostic_fixture(tmp_path)

    assert main(["doctor", str(tmp_path)]) == 0
    captured = capsys.readouterr()

    assert captured.err == ""
    assert "RepoInvariant doctor" in captured.out
    assert "env" in captured.out
    assert "features" in captured.out
    assert "versions" in captured.out
    assert "all_ignored" in captured.out
    assert "ENV001: error (active)" in captured.out
    assert "TRACE004: off (inactive: rule_off)" in captured.out
    assert "compose.yml" not in captured.out
    assert "runtime.env" not in captured.out
    assert ".java-version" not in captured.out


def test_doctor_text_marks_required_version_source_ranges(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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
versions:
  java:
    expected: "21"
    gradle: []
    dockerfiles: []
    compose: []
    workflows: []
    docs: []
    ignore: []
    required: [gradle]
""",
    )

    assert main(["doctor", str(tmp_path)]) == 0
    captured = capsys.readouterr()

    assert captured.err == ""
    assert "gradle: empty_patterns; required;" in captured.out


def test_doctor_verbose_is_deterministic_and_preserves_duplicate_pattern_count(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path, "contract.env", "SYNTHETIC=private-canary\n")
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [contract.env, contract.env]
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
""",
    )

    assert main(["doctor", str(tmp_path), "--format", "json", "--verbose"]) == 0
    first = capsys.readouterr().out
    assert main(["doctor", str(tmp_path), "--format", "json", "--verbose"]) == 0
    second = capsys.readouterr().out

    assert first == second
    payload = json.loads(first)
    contracts = _by_name(_by_name(payload["scanners"])["env"]["sources"])["contracts"]
    assert contracts["patterns"] == {
        "count": 2,
        "omitted_count": 0,
        "values": ["contract.env", "contract.env"],
    }
    assert contracts["matched"]["count"] == 1
    assert "private-canary" not in first


def test_doctor_bounds_ignored_inventory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(3):
        _write(tmp_path, f"ignored/{index}.env", "PRIVATE=not-reported\n")
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [ignored/*.env]
  compose: []
  kubernetes: []
  workflows: []
  spring: []
  ignore: [ignored/**]
features:
  requirements: []
  specifications: []
  tests: []
  ignore: []
""",
    )
    monkeypatch.setattr("repoinvariant.diagnostics.MAX_SCAN_FILES", 2)

    assert main(["doctor", str(tmp_path), "--format", "json", "--verbose"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "exceeds 2 unique files" in captured.err
    assert "not-reported" not in captured.err


def test_doctor_verbose_escapes_control_characters_and_never_prints_contents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    filename = "evil\n::error title=injected::boom.md"
    _write(tmp_path, f"docs/{filename}", "DOCTOR_SECRET_CANARY\n")
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
  requirements: [docs/**/*]
  specifications: []
  tests: []
  ignore: []
""",
    )

    assert main(["doctor", str(tmp_path), "--format", "json", "--verbose"]) == 0
    json_output = capsys.readouterr().out
    assert "DOCTOR_SECRET_CANARY" not in json_output
    assert "evil\\n::error title=injected::boom.md" in json_output
    assert "\n::error title=injected::boom.md" not in json_output
    json.loads(json_output)

    assert main(["doctor", str(tmp_path), "--verbose"]) == 0
    text_output = capsys.readouterr().out
    assert "DOCTOR_SECRET_CANARY" not in text_output
    assert "evil\\x0a::error title=injected::boom.md" in text_output
    assert "\n::error title=injected::boom.md" not in text_output


def test_doctor_verbose_budget_accounts_for_escaped_output_size(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filename = "\x1b" * 30 + ".env"
    _write(tmp_path, f"files/{filename}", "PRIVATE=not-reported\n")
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [files/*.env]
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
""",
    )
    monkeypatch.setattr("repoinvariant.doctor.MAX_VERBOSE_BYTES", 80)

    exit_code, payload, error = _doctor_json(tmp_path, capsys, "--verbose")

    assert exit_code == 0
    assert error == ""
    assert payload["verbose"]["truncated"] is True
    env_files = _by_name(payload["scanners"])["env"]["files"]
    assert env_files == {"count": 1, "paths": [], "omitted_count": 1}
    assert filename not in json.dumps(payload)


def test_doctor_rejects_configured_symlink_and_malformed_scanner_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path, "real.md", "# REQ-SAFE\n")
    linked = tmp_path / "linked.md"
    try:
        linked.symlink_to(tmp_path / "real.md")
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
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
  requirements: [linked.md]
  specifications: []
  tests: []
  ignore: []
""",
    )

    assert main(["doctor", str(tmp_path), "--format", "json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "symbolic link" in captured.err

    linked.unlink()
    _write(tmp_path, "real-env/contract.env", "PRIVATE=not-reported\n")
    linked_directory = tmp_path / "linked-env"
    linked_directory.symlink_to(tmp_path / "real-env", target_is_directory=True)
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [linked-env/*.env]
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
""",
    )

    assert main(["doctor", str(tmp_path), "--format", "json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "environment file path is unsafe" in captured.err

    linked_directory.unlink()
    _write(tmp_path, "compose.yml", "services: [unterminated\n")
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: []
  compose: [compose.yml]
  kubernetes: []
  workflows: []
  spring: []
  ignore: []
features:
  requirements: []
  specifications: []
  tests: []
  ignore: []
""",
    )

    assert main(["doctor", str(tmp_path), "--format", "json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid YAML" in captured.err


def test_doctor_skips_configured_fifo_without_opening_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable")
    fifo = tmp_path / "contract.env"
    os.mkfifo(fifo)
    _write(
        tmp_path,
        ".repoinvariant.yml",
        """version: 1
env:
  contracts: [contract.env]
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
""",
    )

    exit_code, payload, error = _doctor_json(tmp_path, capsys, "--verbose")

    assert exit_code == 0
    assert error == ""
    contracts = _by_name(_by_name(payload["scanners"])["env"]["sources"])["contracts"]
    assert contracts["state"] == "no_matches"
    assert contracts["matched"]["count"] == 0
