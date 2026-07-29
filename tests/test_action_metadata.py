from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_action_exposes_github_feedback_outputs() -> None:
    metadata = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    outputs = metadata["outputs"]

    assert set(outputs) == {"status", "errors", "warnings", "report-path"}
    for name in outputs:
        assert outputs[name]["value"] == f"${{{{ steps.check.outputs.{name} }}}}"

    check_step = next(step for step in metadata["runs"]["steps"] if step.get("id") == "check")
    assert "--github-actions" in check_step["run"]
    assert "${{ inputs." not in check_step["run"]


def test_dogfood_workflow_consumes_report_output() -> None:
    workflow = (ROOT / ".github" / "workflows" / "repoinvariant.yml").read_text(
        encoding="utf-8"
    )

    assert "id: repoinvariant" in workflow
    assert "steps.repoinvariant.outputs.report-path" in workflow
    assert "id: warning_fixture" in workflow
    assert "continue-on-error: true" in workflow
    assert "path: tests/fixtures/action-warning" in workflow
    assert "steps.warning_fixture.outcome" in workflow
    assert "steps.warning_fixture.outputs.warnings" in workflow
