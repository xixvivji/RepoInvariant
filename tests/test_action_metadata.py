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
    assert check_step["env"]["REPOINVARIANT_NO_VERSIONS"] == (
        "${{ inputs.no-versions }}"
    )
    assert '--baseline=$REPOINVARIANT_BASELINE' in check_step["run"]
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


def test_dogfood_workflow_consumes_report_output() -> None:
    workflow = (ROOT / ".github" / "workflows" / "repoinvariant.yml").read_text(
        encoding="utf-8"
    )

    assert "id: repoinvariant" in workflow
    assert "steps.repoinvariant.outputs.report-path" in workflow
    assert "id: warning_fixture" in workflow
    assert "id: baseline_fixture" in workflow
    assert "baseline: .repoinvariant-baseline.json" in workflow
    assert "continue-on-error: true" in workflow
    assert "path: tests/fixtures/action-warning" in workflow
    assert "steps.warning_fixture.outcome" in workflow
    assert "steps.warning_fixture.outputs.warnings" in workflow
    assert "steps.baseline_fixture.outputs.warnings" in workflow
