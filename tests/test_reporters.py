import json
from pathlib import Path

from repotruth.models import Finding, Location, ScanResult, Severity
from repotruth.reporters import render_json, render_markdown, render_sarif, render_text


def _result() -> ScanResult:
    return ScanResult(
        findings=[
            Finding(
                code="ENV001",
                message="DATABASE_URL is used but missing from the environment contract",
                severity=Severity.ERROR,
                location=Location(Path("compose.yml"), 7, 5),
                hint="Declare DATABASE_URL in a contract file.",
            ),
            Finding(
                code="ENV002",
                message="OLD_FLAG is declared but unused",
                severity=Severity.WARNING,
                location=Location(Path(".env.example"), 3, 1),
            ),
        ],
        scanned_files={Path("compose.yml"), Path(".env.example")},
    )


def test_text_report_has_stable_locations_and_summary(tmp_path: Path) -> None:
    report = render_text(_result(), tmp_path)

    assert "compose.yml:7:5: error ENV001" in report
    assert report.endswith("FAIL: 2 files, 1 errors, 1 warnings\n")


def test_json_report_is_machine_readable(tmp_path: Path) -> None:
    payload = json.loads(render_json(_result(), tmp_path))

    assert payload["ok"] is False
    assert payload["summary"] == {"errors": 1, "files": 2, "notes": 0, "warnings": 1}
    assert payload["findings"][0]["location"]["path"] == "compose.yml"


def test_markdown_report_escapes_table_content(tmp_path: Path) -> None:
    result = _result()
    result.findings[0] = Finding(
        code="ENV001",
        message="A | B",
        severity=Severity.ERROR,
        location=Location(Path("compose.yml"), 1, 1),
    )

    assert "A \\| B" in render_markdown(result, tmp_path)


def test_sarif_report_contains_rule_and_region(tmp_path: Path) -> None:
    payload = json.loads(render_sarif(_result(), tmp_path))
    run = payload["runs"][0]

    assert payload["version"] == "2.1.0"
    assert run["tool"]["driver"]["name"] == "RepoTruth"
    assert run["results"][0]["ruleId"] == "ENV001"
    assert run["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 7
