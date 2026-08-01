import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repoinvariant import __version__
from repoinvariant.cli import main

PLUGIN_FIXTURE_SITE = Path(__file__).parent / "fixtures" / "plugins" / "site-packages"


@pytest.mark.parametrize("module_name", ["pathlib", "yaml"])
@pytest.mark.parametrize("shadow_source", ["working-directory", "pythonpath"])
def test_python_m_bootstrap_does_not_import_working_tree_shadows(
    tmp_path: Path, module_name: str, shadow_source: str
) -> None:
    working_directory = tmp_path / "repository"
    working_directory.mkdir()
    shadow_root = (
        tmp_path / "pythonpath" if shadow_source == "pythonpath" else working_directory
    )
    shadow_root.mkdir(exist_ok=True)
    marker = tmp_path / f"{module_name}-executed"
    (shadow_root / f"{module_name}.py").write_text(
        f"open({str(marker)!r}, 'w').write('executed')\n"
        "raise RuntimeError('LOCAL_SHADOW_EXECUTED')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    if shadow_source == "pythonpath":
        environment["PYTHONPATH"] = str(shadow_root)

    completed = subprocess.run(
        [sys.executable, "-m", "repoinvariant", "--version"],
        cwd=working_directory,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"repoinvariant {__version__}\n"
    assert not marker.exists()


def _write_baseline_fixture(root: Path) -> None:
    (root / ".env.example").write_text("DECLARED=\n", encoding="utf-8")
    (root / "compose.yml").write_text(
        "services:\n  api:\n    environment:\n      REQUIRED: ${REQUIRED}\n",
        encoding="utf-8",
    )


def _write_plugin_fixture(root: Path, marker: str = "TODO") -> None:
    (root / "checks.todo").write_text("OK\nTODO\n", encoding="utf-8")
    (root / ".repoinvariant.yml").write_text(
        f"""version: 1
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
plugins:
  sample.todo:
    config:
      marker: {marker}
      patterns: ["**/*.todo"]
    rules:
      TODO001: error
""",
        encoding="utf-8",
    )


def test_init_creates_config_and_refuses_to_overwrite(tmp_path: Path, capsys) -> None:
    assert main(["init", str(tmp_path)]) == 0
    config = tmp_path / ".repoinvariant.yml"
    assert config.exists()

    assert main(["init", str(tmp_path)]) == 2
    assert "already exists" in capsys.readouterr().err


def test_check_clean_example_as_json(capsys) -> None:
    example = Path(__file__).parents[1] / "examples" / "ticket-service"

    assert main(["check", str(example), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"] == {"errors": 0, "files": 6, "notes": 0, "warnings": 0}


def test_drift_example_reports_expected_contract_breaks(capsys) -> None:
    example = Path(__file__).parents[1] / "examples" / "ticket-service-drift"

    assert main(["check", str(example), "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["summary"] == {"errors": 2, "files": 6, "notes": 0, "warnings": 0}
    assert [
        (finding["code"], finding["severity"], finding["location"])
        for finding in payload["findings"]
    ] == [
        ("ENV001", "error", {"path": "compose.yml", "line": 7, "column": 25}),
        ("TRACE003", "error", {"path": "openapi.yml", "line": 8, "column": 21}),
    ]


def test_check_returns_one_for_drift(tmp_path: Path, capsys) -> None:
    (tmp_path / ".env.example").write_text(
        "DATABASE_URL=postgres://localhost/db\n", encoding="utf-8"
    )
    (tmp_path / "compose.yml").write_text(
        "services:\n  api:\n    environment:\n      REDIS_URL: ${REDIS_URL}\n",
        encoding="utf-8",
    )
    (tmp_path / ".repoinvariant.yml").write_text(
        """version: 1
env:
  contracts: [.env.example]
  compose: [compose.yml]
  kubernetes: []
  workflows: []
  spring: []
  ignore: []
features:
  requirements: []
  specifications: []
  tests: []
  id_pattern: '\\bREQ-[A-Z0-9][A-Z0-9-]*\\b'
  openapi_extension: x-feature-id
  ignore: []
""",
        encoding="utf-8",
    )

    assert main(["check", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "ENV001" in output
    assert "REDIS_URL" in output
    assert "postgres://" not in output


def test_check_runs_opt_in_version_contract_and_can_skip_it(tmp_path: Path, capsys) -> None:
    (tmp_path / "build.gradle.kts").write_text(
        "kotlin { jvmToolchain(17) }\n",
        encoding="utf-8",
    )
    (tmp_path / ".repoinvariant.yml").write_text(
        """version: 1
versions:
  java:
    expected: "21"
    gradle: [build.gradle.kts]
    dockerfiles: []
    compose: []
    workflows: []
    docs: []
    required: [gradle]
""",
        encoding="utf-8",
    )

    assert main(["check", str(tmp_path), "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert [(item["code"], item["location"]["path"]) for item in payload["findings"]] == [
        ("VER001", "build.gradle.kts")
    ]

    assert main(["check", str(tmp_path), "--no-versions", "--format", "json"]) == 0
    skipped = json.loads(capsys.readouterr().out)
    assert skipped["findings"] == []


def test_explicit_plugin_check_and_baseline_are_scope_bound(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.syspath_prepend(str(PLUGIN_FIXTURE_SITE))
    _write_plugin_fixture(tmp_path)
    common = ["--no-env", "--no-features", "--no-versions", "--plugin", "sample.todo"]

    assert main(["check", str(tmp_path), *common, "--format", "json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert [(item["code"], item["location"]["path"]) for item in payload["findings"]] == [
        ("sample.todo:TODO001", "checks.todo")
    ]

    assert main(["baseline", str(tmp_path), *common]) == 0
    baseline_text = (tmp_path / ".repoinvariant-baseline.json").read_text(encoding="utf-8")
    assert "sample.todo:TODO001" in baseline_text
    assert "checks.todo" not in baseline_text
    capsys.readouterr()

    assert (
        main(
            [
                "check",
                str(tmp_path),
                *common,
                "--baseline",
                ".repoinvariant-baseline.json",
            ]
        )
        == 0
    )
    assert "1 suppressed" in capsys.readouterr().err

    assert (
        main(
            [
                "doctor",
                str(tmp_path),
                *common,
                "--baseline",
                ".repoinvariant-baseline.json",
                "--format",
                "json",
            ]
        )
        == 0
    )
    doctor_payload = json.loads(capsys.readouterr().out)
    assert doctor_payload["baseline"]["status"] == "match"

    assert (
        main(
            [
                "doctor",
                str(tmp_path),
                "--no-env",
                "--no-features",
                "--no-versions",
                "--baseline",
                ".repoinvariant-baseline.json",
                "--format",
                "json",
            ]
        )
        == 0
    )
    doctor_without_plugin = json.loads(capsys.readouterr().out)
    assert doctor_without_plugin["baseline"]["status"] == "mismatch"

    assert (
        main(
            [
                "check",
                str(tmp_path),
                "--no-env",
                "--no-features",
                "--no-versions",
                "--baseline",
                ".repoinvariant-baseline.json",
            ]
        )
        == 2
    )
    assert "scope does not match" in capsys.readouterr().err

    _write_plugin_fixture(tmp_path, marker="FIXME")
    assert (
        main(
            [
                "check",
                str(tmp_path),
                *common,
                "--baseline",
                ".repoinvariant-baseline.json",
            ]
        )
        == 2
    )
    assert "scope does not match" in capsys.readouterr().err


def test_plugin_cli_missing_duplicate_and_exception_paths_return_two(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.syspath_prepend(str(PLUGIN_FIXTURE_SITE))

    assert main(["check", str(tmp_path), "--plugin", "missing.plugin"]) == 2
    assert "not installed" in capsys.readouterr().err

    assert (
        main(
            [
                "check",
                str(tmp_path),
                "--plugin",
                "sample.todo",
                "--plugin",
                "sample.todo",
            ]
        )
        == 2
    )
    assert "duplicate ID" in capsys.readouterr().err

    assert main(["check", str(tmp_path), "--plugin", "sample.crash"]) == 2
    error = capsys.readouterr().err
    assert "failed during scanning" in error
    assert "PRIVATE_FIXTURE_EXCEPTION_VALUE" not in error


def test_check_escapes_control_characters_in_errors(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    def fail(*args, **kwargs):
        del args, kwargs
        raise ValueError("bad path\n::error title=injected::boom\u2028end")

    monkeypatch.setattr("repoinvariant.cli._scan", fail)

    assert main(["check", str(tmp_path), "--github-actions"]) == 2
    error = capsys.readouterr().err
    assert "\n::error" not in error
    assert "bad path\\x0a::error title=injected::boom\\u2028end" in error


def test_version_findings_are_baseline_scoped_with_no_versions(tmp_path: Path, capsys) -> None:
    (tmp_path / "build.gradle.kts").write_text(
        "kotlin { jvmToolchain(17) }\n",
        encoding="utf-8",
    )
    (tmp_path / ".repoinvariant.yml").write_text(
        """version: 1
versions:
  java:
    expected: "21"
    gradle: [build.gradle.kts]
    dockerfiles: []
    compose: []
    workflows: []
    docs: []
""",
        encoding="utf-8",
    )
    common = ["--no-env", "--no-features"]

    assert main(["baseline", str(tmp_path), *common]) == 0
    baseline_text = (tmp_path / ".repoinvariant-baseline.json").read_text(encoding="utf-8")
    assert "build.gradle" not in baseline_text
    assert "Java version" not in baseline_text
    capsys.readouterr()

    assert (
        main(
            [
                "check",
                str(tmp_path),
                *common,
                "--baseline",
                ".repoinvariant-baseline.json",
            ]
        )
        == 0
    )
    assert "1 suppressed" in capsys.readouterr().err

    assert (
        main(
            [
                "check",
                str(tmp_path),
                *common,
                "--no-versions",
                "--baseline",
                ".repoinvariant-baseline.json",
            ]
        )
        == 2
    )
    assert "scope does not match" in capsys.readouterr().err


def test_check_accepts_option_like_repository_path(tmp_path: Path, capsys) -> None:
    root = tmp_path / "--help"
    root.mkdir()

    assert main(["check", "--format", "json", "--", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_baseline_accepts_option_like_repository_path(tmp_path: Path, capsys) -> None:
    root = tmp_path / "--help"
    root.mkdir()

    assert main(["baseline", "--no-env", "--no-features", "--", str(root)]) == 0
    assert (root / ".repoinvariant-baseline.json").is_file()
    assert "0 accepted finding(s)" in capsys.readouterr().out


def test_check_fails_for_explicit_missing_config(tmp_path: Path, capsys) -> None:
    assert main(["check", str(tmp_path), "--config", "missing.yml"]) == 2
    assert "configuration file does not exist" in capsys.readouterr().err


def test_check_without_baseline_keeps_original_streams_and_exit_code(
    tmp_path: Path, capsys
) -> None:
    assert main(["check", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert captured.out == "PASS: 0 files, 0 errors, 0 warnings\n"
    assert captured.err == ""


def test_baseline_snapshots_drift_and_check_reports_only_new_findings(
    tmp_path: Path, capsys
) -> None:
    _write_baseline_fixture(tmp_path)

    assert main(["baseline", str(tmp_path)]) == 0
    creation = capsys.readouterr()
    assert "2 accepted finding(s)" in creation.out
    assert "trusted base branch" in creation.err
    assert "review every baseline change" in creation.err
    baseline_path = tmp_path / ".repoinvariant-baseline.json"
    baseline_text = baseline_path.read_text(encoding="utf-8")
    assert "REQUIRED" not in baseline_text
    assert "DECLARED" not in baseline_text

    assert main(["check", str(tmp_path), "--baseline", baseline_path.name]) == 0
    captured = capsys.readouterr()
    assert "PASS:" in captured.out
    assert "2 suppressed, 0 stale" in captured.err

    (tmp_path / "compose.yml").write_text(
        "services:\n  api:\n    environment:\n"
        "      REQUIRED: ${REQUIRED}\n      ADDED: ${ADDED}\n",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "check",
                str(tmp_path),
                "--baseline",
                baseline_path.name,
                "--format",
                "json",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert [(finding["code"], finding["message"]) for finding in payload["findings"]] == [
        ("ENV001", "Environment variable 'ADDED' is consumed but missing from the contract.")
    ]
    assert "2 suppressed, 0 stale" in captured.err


def test_baseline_stale_entries_are_nonblocking(tmp_path: Path, capsys) -> None:
    _write_baseline_fixture(tmp_path)
    assert main(["baseline", str(tmp_path)]) == 0
    capsys.readouterr()

    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    (tmp_path / "compose.yml").write_text("services: {}\n", encoding="utf-8")

    assert main(["check", str(tmp_path), "--baseline", ".repoinvariant-baseline.json"]) == 0
    captured = capsys.readouterr()
    assert "0 suppressed, 2 stale" in captured.err
    assert "regenerate the baseline" in captured.err


def test_baseline_refuses_overwrite_without_force_and_config_collision(
    tmp_path: Path, capsys
) -> None:
    _write_baseline_fixture(tmp_path)
    assert main(["baseline", str(tmp_path)]) == 0
    baseline_path = tmp_path / ".repoinvariant-baseline.json"
    original = baseline_path.read_text(encoding="utf-8")
    capsys.readouterr()

    assert main(["baseline", str(tmp_path)]) == 2
    assert baseline_path.read_text(encoding="utf-8") == original
    assert "use --force" in capsys.readouterr().err

    assert main(["baseline", str(tmp_path), "--force"]) == 0
    assert main(["baseline", str(tmp_path), "--output", ".repoinvariant.yml"]) == 2
    assert "must be different paths" in capsys.readouterr().err
    assert main(["baseline", str(tmp_path), "--output", ".REPOINVARIANT.YML"]) == 2
    assert "must be different paths" in capsys.readouterr().err


def test_check_refuses_to_overwrite_its_baseline_with_report(
    tmp_path: Path, capsys
) -> None:
    _write_baseline_fixture(tmp_path)
    assert main(["baseline", str(tmp_path)]) == 0
    baseline_path = tmp_path / ".repoinvariant-baseline.json"
    original = baseline_path.read_text(encoding="utf-8")
    capsys.readouterr()

    assert (
        main(
            [
                "check",
                str(tmp_path),
                "--baseline",
                baseline_path.name,
                "--output",
                baseline_path.name,
            ]
        )
        == 2
    )
    assert baseline_path.read_text(encoding="utf-8") == original
    assert "must be different paths" in capsys.readouterr().err


def test_check_baseline_input_errors_return_two(tmp_path: Path, capsys) -> None:
    assert main(["check", str(tmp_path), "--baseline", "missing.json"]) == 2
    assert "cannot read baseline" in capsys.readouterr().err

    baseline_path = tmp_path / ".repoinvariant-baseline.json"
    baseline_path.write_text("{}\n", encoding="utf-8")
    assert main(["check", str(tmp_path), "--baseline", baseline_path.name]) == 2
    assert "missing key" in capsys.readouterr().err

    assert main(["baseline", str(tmp_path), "--force"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "check",
                str(tmp_path),
                "--baseline",
                baseline_path.name,
                "--no-features",
            ]
        )
        == 2
    )
    assert "scope does not match" in capsys.readouterr().err

    assert (
        main(
            [
                "check",
                str(tmp_path),
                "--config",
                ".repoinvariant.yml",
                "--baseline",
                ".repoinvariant.yml",
            ]
        )
        == 2
    )
    assert "must be different paths" in capsys.readouterr().err


def test_warning_failure_policy_matches_json_report(tmp_path: Path, capsys) -> None:
    (tmp_path / ".env.example").write_text("UNUSED=\n", encoding="utf-8")

    arguments = [
        "check",
        str(tmp_path),
        "--no-features",
        "--fail-on",
        "warning",
        "--format",
        "json",
    ]
    assert main(arguments) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["blocking_threshold"] == "warning"


def test_output_and_force_init_refuse_symlinks(tmp_path: Path, capsys) -> None:
    outside_config = tmp_path / "outside.yml"
    outside_config.write_text("do not replace\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".repoinvariant.yml").symlink_to(outside_config)

    assert main(["init", str(root), "--force"]) == 2
    assert outside_config.read_text(encoding="utf-8") == "do not replace\n"
    assert "symbolic link" in capsys.readouterr().err

    (root / ".repoinvariant.yml").unlink()
    output_target = tmp_path / "outside.json"
    output_target.write_text("do not replace\n", encoding="utf-8")
    (root / "report.json").symlink_to(output_target)

    assert main(["check", str(root), "--format", "json", "--output", "report.json"]) == 2
    assert output_target.read_text(encoding="utf-8") == "do not replace\n"
    assert "symbolic link" in capsys.readouterr().err

    baseline_target = tmp_path / "outside-baseline.json"
    baseline_target.write_text("do not replace\n", encoding="utf-8")
    (root / "baseline.json").symlink_to(baseline_target)

    assert (
        main(
            [
                "baseline",
                str(root),
                "--output",
                "baseline.json",
                "--force",
                "--no-env",
                "--no-features",
            ]
        )
        == 2
    )
    assert baseline_target.read_text(encoding="utf-8") == "do not replace\n"
    assert "symbolic link" in capsys.readouterr().err


def test_github_actions_feedback_keeps_json_separate_from_commands(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    (tmp_path / ".env.example").write_text("UNUSED=\n", encoding="utf-8")
    github_output = tmp_path / "github-output"
    step_summary = tmp_path / "step-summary"
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))

    assert main(["check", str(tmp_path), "--format", "json", "--github-actions"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["ok"] is True
    assert payload["summary"]["warnings"] == 1
    assert "::warning" in captured.out
    assert "::" not in captured.err
    outputs = github_output.read_text(encoding="utf-8")
    assert "\n0\n" in outputs
    assert "\n1\n" in outputs
    assert "\npass\n" in outputs
    assert "report-path<<" in outputs
    assert "# RepoInvariant report" in step_summary.read_text(encoding="utf-8")


def test_github_actions_baseline_feedback_and_outputs_include_only_new_findings(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _write_baseline_fixture(tmp_path)
    assert main(["baseline", str(tmp_path)]) == 0
    capsys.readouterr()
    (tmp_path / "compose.yml").write_text(
        "services:\n  api:\n    environment:\n"
        "      REQUIRED: ${REQUIRED}\n      ADDED: ${ADDED}\n",
        encoding="utf-8",
    )
    github_output = tmp_path / "github-output"
    step_summary = tmp_path / "step-summary"
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary))

    assert (
        main(
            [
                "check",
                str(tmp_path),
                "--baseline",
                ".repoinvariant-baseline.json",
                "--format",
                "json",
                "--github-actions",
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert [finding["message"] for finding in payload["findings"]] == [
        "Environment variable 'ADDED' is consumed but missing from the contract."
    ]
    assert "::notice title=RepoInvariant baseline::2 suppressed, 0 stale." in captured.out
    assert "::error" in captured.out
    assert "ADDED" in captured.out
    assert "REQUIRED" not in captured.out
    outputs = github_output.read_text(encoding="utf-8")
    assert "\n1\n" in outputs
    assert "\n0\n" in outputs
    assert "\nfail\n" in outputs
    summary = step_summary.read_text(encoding="utf-8")
    assert "ADDED" in summary
    assert "REQUIRED" not in summary


def test_github_actions_feedback_file_error_returns_two(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    github_output = tmp_path / "github-output-directory"
    github_output.mkdir()
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "step-summary"))

    assert main(["check", str(tmp_path), "--github-actions"]) == 2
    assert "repoinvariant:" in capsys.readouterr().err
