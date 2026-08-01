import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
CODEQL_UPLOAD = (
    "github/codeql-action/upload-sarif@"
    "f205ea1c3313d32999d8d6a48b4f6530d4437b38 # v4.37.4"
)
REPOINVARIANT_ACTION = (
    "xixvivji/RepoInvariant@"
    "d045e7844f636b20473efeff4e9f62cbfcf16690 # v0.5.0"
)


def _workflow_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_consumer_workflow_is_copyable_pinned_and_fork_safe() -> None:
    path = ROOT / "examples" / "github" / "repoinvariant.yml"
    workflow = _workflow_text(path)
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)

    assert parsed["permissions"] == {"contents": "read", "security-events": "write"}
    assert parsed["jobs"]["contracts"]["name"] == "RepoInvariant contracts"
    assert "pull_request_target" not in workflow
    assert "persist-credentials: false" in workflow
    assert parsed["jobs"]["contracts"]["steps"][1]["with"]["strict"] == "true"
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "steps.repoinvariant.outputs.report-path != ''" in workflow
    assert CODEQL_UPLOAD in workflow
    assert REPOINVARIANT_ACTION in workflow

    references = re.findall(r"^\s*uses:\s+([^@\s]+)@([^\s]+)", workflow, re.MULTILINE)
    assert {name for name, _ in references} == {
        "actions/checkout",
        "actions/upload-artifact",
        "github/codeql-action/upload-sarif",
        "xixvivji/RepoInvariant",
    }
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for _, revision in references)


def test_installation_document_embeds_the_tested_consumer_workflow() -> None:
    fixture = _workflow_text(
        ROOT / "examples" / "github" / "repoinvariant.yml"
    ).rstrip()
    document = _workflow_text(ROOT / "docs" / "github-installation.md")
    embedded = document.split(
        "<!-- consumer-workflow:start -->\n```yaml\n", 1
    )[1].split("\n```\n<!-- consumer-workflow:end -->", 1)[0]

    assert embedded == fixture
    assert "RepoInvariant contracts" in document
    assert "Require review from Code Owners" in document
    assert "doctor --strict --baseline" in document
    assert '`strict: "true"` runs `doctor --strict`' in document
    assert "25,000 results" in document
    assert "10 MiB" in document
    assert "pull_request_target" in document
    assert "official immutable [`v4.37.4` release]" in document


def test_dogfood_workflow_uploads_sarif_only_with_safe_write_context() -> None:
    workflow = _workflow_text(ROOT / ".github" / "workflows" / "repoinvariant.yml")
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)

    assert parsed["permissions"] == {"contents": "read", "security-events": "write"}
    assert parsed["jobs"]["contracts"]["steps"][1]["with"]["strict"] == "true"
    assert CODEQL_UPLOAD in workflow
    assert "steps.repoinvariant.outputs.report-path != ''" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "category: repoinvariant" in workflow
