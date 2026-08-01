import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


def test_action_exposes_github_feedback_outputs() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    assert metadata["inputs"]["baseline"] == {
        "description": "Optional repository-relative baseline file for gradual adoption.",
        "required": False,
        "default": "",
    }
    assert metadata["inputs"]["strict"] == {
        "description": "Run doctor --strict before checking contracts.",
        "required": False,
        "default": "false",
    }
    assert metadata["inputs"]["no-versions"] == {
        "description": "Set to true to skip Java version-contract checks.",
        "required": False,
        "default": "false",
    }
    outputs = metadata["outputs"]

    assert set(outputs) == {"status", "errors", "warnings", "report-path"}
    for name in outputs:
        assert outputs[name]["value"] == f"${{{{ steps.check.outputs.{name} }}}}"

    check_step = next(step for step in metadata["runs"]["steps"] if step.get("id") == "check")
    assert check_step["env"]["REPOINVARIANT_BASELINE"] == "${{ inputs.baseline }}"
    assert check_step["env"]["REPOINVARIANT_STRICT"] == "${{ inputs.strict }}"
    assert check_step["env"]["REPOINVARIANT_NO_VERSIONS"] == (
        "${{ inputs.no-versions }}"
    )
    assert '--baseline=$REPOINVARIANT_BASELINE' in check_step["run"]
    assert "doctor --strict" in check_step["run"]
    assert "--no-versions" in check_step["run"]
    assert "--github-actions" in check_step["run"]
    assert "${{ inputs." not in check_step["run"]


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("true", True)])
def test_action_forwards_valid_no_versions_boolean(
    tmp_path: Path, value: str, expected: bool
) -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    check_step = next(step for step in metadata["runs"]["steps"] if step.get("id") == "check")
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    captured_argv = tmp_path / "argv.txt"
    fake_uv = executable_dir / "uv"
    fake_uv.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$REPOINVARIANT_TEST_ARGV\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{executable_dir}{os.pathsep}{os.environ['PATH']}",
        "REPOINVARIANT_PATH": "repository path",
        "REPOINVARIANT_CONFIG": "",
        "REPOINVARIANT_FORMAT": "text",
        "REPOINVARIANT_OUTPUT": "",
        "REPOINVARIANT_BASELINE": "",
        "REPOINVARIANT_FAIL_ON": "error",
        "REPOINVARIANT_STRICT": "false",
        "REPOINVARIANT_NO_ENV": "false",
        "REPOINVARIANT_NO_FEATURES": "false",
        "REPOINVARIANT_NO_VERSIONS": value,
        "REPOINVARIANT_ACTION_PATH": str(ROOT),
        "REPOINVARIANT_TEST_ARGV": str(captured_argv),
    }

    completed = subprocess.run(
        ["bash", "-c", check_step["run"]],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    argv = captured_argv.read_text(encoding="utf-8").splitlines()
    assert ("--no-versions" in argv) is expected
    assert argv[-2:] == ["--", "repository path"]


def test_action_strict_runs_doctor_before_check_with_shared_selection(
    tmp_path: Path,
) -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    check_step = next(step for step in metadata["runs"]["steps"] if step.get("id") == "check")
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    captured_argv = tmp_path / "argv.txt"
    fake_uv = executable_dir / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "{ printf '%s\\n' __CALL__; printf '%s\\n' \"$@\"; } "
        '>> "$REPOINVARIANT_TEST_ARGV"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{executable_dir}{os.pathsep}{os.environ['PATH']}",
        "REPOINVARIANT_PATH": "repository path",
        "REPOINVARIANT_CONFIG": "config path.yml",
        "REPOINVARIANT_FORMAT": "sarif",
        "REPOINVARIANT_OUTPUT": "report path.sarif",
        "REPOINVARIANT_BASELINE": "baseline path.json",
        "REPOINVARIANT_FAIL_ON": "warning",
        "REPOINVARIANT_STRICT": "true",
        "REPOINVARIANT_NO_ENV": "true",
        "REPOINVARIANT_NO_FEATURES": "false",
        "REPOINVARIANT_NO_VERSIONS": "true",
        "REPOINVARIANT_ACTION_PATH": str(ROOT),
        "REPOINVARIANT_TEST_ARGV": str(captured_argv),
    }

    completed = subprocess.run(
        ["bash", "-c", check_step["run"]],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    calls = [
        block.strip().splitlines()
        for block in captured_argv.read_text(encoding="utf-8").split("__CALL__\n")
        if block.strip()
    ]
    assert len(calls) == 2
    command_prefix = ["run", "--project", str(ROOT), "--frozen", "repoinvariant"]
    assert calls[0] == [
        *command_prefix,
        "doctor",
        "--strict",
        "--config=config path.yml",
        "--baseline=baseline path.json",
        "--no-env",
        "--no-versions",
        "--",
        "repository path",
    ]
    assert calls[1] == [
        *command_prefix,
        "check",
        "--github-actions",
        "--format=sarif",
        "--fail-on=warning",
        "--config=config path.yml",
        "--baseline=baseline path.json",
        "--no-env",
        "--no-versions",
        "--output=report path.sarif",
        "--",
        "repository path",
    ]


def test_action_strict_failure_stops_before_check(tmp_path: Path) -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    check_step = next(step for step in metadata["runs"]["steps"] if step.get("id") == "check")
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    captured_argv = tmp_path / "argv.txt"
    fake_uv = executable_dir / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" >> \"$REPOINVARIANT_TEST_ARGV\"\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{executable_dir}{os.pathsep}{os.environ['PATH']}",
        "REPOINVARIANT_PATH": ".",
        "REPOINVARIANT_CONFIG": "",
        "REPOINVARIANT_FORMAT": "text",
        "REPOINVARIANT_OUTPUT": "",
        "REPOINVARIANT_BASELINE": "",
        "REPOINVARIANT_FAIL_ON": "error",
        "REPOINVARIANT_STRICT": "true",
        "REPOINVARIANT_NO_ENV": "false",
        "REPOINVARIANT_NO_FEATURES": "false",
        "REPOINVARIANT_NO_VERSIONS": "false",
        "REPOINVARIANT_ACTION_PATH": str(ROOT),
        "REPOINVARIANT_TEST_ARGV": str(captured_argv),
    }

    completed = subprocess.run(
        ["bash", "-c", check_step["run"]],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 1
    argv = captured_argv.read_text(encoding="utf-8").splitlines()
    assert "doctor" in argv
    assert "check" not in argv


def test_action_rejects_invalid_no_versions_boolean(tmp_path: Path) -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    check_step = next(step for step in metadata["runs"]["steps"] if step.get("id") == "check")
    environment = {
        **os.environ,
        "REPOINVARIANT_PATH": ".",
        "REPOINVARIANT_CONFIG": "",
        "REPOINVARIANT_FORMAT": "text",
        "REPOINVARIANT_OUTPUT": "",
        "REPOINVARIANT_BASELINE": "",
        "REPOINVARIANT_FAIL_ON": "error",
        "REPOINVARIANT_STRICT": "false",
        "REPOINVARIANT_NO_ENV": "false",
        "REPOINVARIANT_NO_FEATURES": "false",
        "REPOINVARIANT_NO_VERSIONS": "TRUE",
        "REPOINVARIANT_ACTION_PATH": str(ROOT),
    }

    completed = subprocess.run(
        ["bash", "-c", check_step["run"]],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=environment,
        text=True,
    )

    assert completed.returncode == 2
    assert "no-versions must be true or false" in completed.stderr


def test_action_rejects_invalid_strict_boolean(tmp_path: Path) -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    check_step = next(step for step in metadata["runs"]["steps"] if step.get("id") == "check")
    environment = {
        **os.environ,
        "REPOINVARIANT_PATH": ".",
        "REPOINVARIANT_CONFIG": "",
        "REPOINVARIANT_FORMAT": "text",
        "REPOINVARIANT_OUTPUT": "",
        "REPOINVARIANT_BASELINE": "",
        "REPOINVARIANT_FAIL_ON": "error",
        "REPOINVARIANT_STRICT": "TRUE",
        "REPOINVARIANT_NO_ENV": "false",
        "REPOINVARIANT_NO_FEATURES": "false",
        "REPOINVARIANT_NO_VERSIONS": "false",
        "REPOINVARIANT_ACTION_PATH": str(ROOT),
    }

    completed = subprocess.run(
        ["bash", "-c", check_step["run"]],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=environment,
        text=True,
    )

    assert completed.returncode == 2
    assert "strict must be true or false" in completed.stderr


def test_dogfood_workflow_consumes_report_output() -> None:
    workflow = (ROOT / ".github" / "workflows" / "repoinvariant.yml").read_text(
        encoding="utf-8"
    )

    assert "id: repoinvariant" in workflow
    assert 'strict: "true"' in workflow
    assert "steps.repoinvariant.outputs.report-path" in workflow
    assert "id: warning_fixture" in workflow
    assert "id: baseline_fixture" in workflow
    assert "baseline: .repoinvariant-baseline.json" in workflow
    assert "continue-on-error: true" in workflow
    assert "path: tests/fixtures/action-warning" in workflow
    assert "steps.warning_fixture.outcome" in workflow
    assert "steps.warning_fixture.outputs.warnings" in workflow
    assert "steps.baseline_fixture.outputs.warnings" in workflow


def test_ci_and_release_workflows_gate_configured_scan_coverage() -> None:
    for relative in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
        workflow = (ROOT / relative).read_text(encoding="utf-8")

        assert "repoinvariant doctor . --strict" in workflow
