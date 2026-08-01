import json
from pathlib import Path

import pytest

from repoinvariant import reporters
from repoinvariant.models import Finding, Location, ScanResult, Severity
from repoinvariant.reporters import render_json, render_markdown, render_sarif, render_text


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
    assert payload["schema_version"] == 1
    assert payload["exit_code"] == 1
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


def test_markdown_report_inerts_html_and_link_syntax(tmp_path: Path) -> None:
    result = ScanResult(
        findings=[
            Finding(
                code="ENV`001",
                message="<details> ![click](https://example.invalid) *bold* _italic_ & done",
                severity=Severity.ERROR,
                location=Location(Path("[source](target).yml"), 1, 1),
            )
        ]
    )

    report = render_markdown(result, tmp_path)

    assert "<details>" not in report
    assert "![click](https://example.invalid)" not in report
    assert "&lt;details&gt;" in report
    assert "&#33;&#91;click&#93;&#40;https://example.invalid&#41;" in report
    assert "| error | ENV&#96;001 |" in report
    assert "&#91;source&#93;&#40;target&#41;.yml" in report


def test_sarif_report_contains_rule_and_region(tmp_path: Path) -> None:
    payload = json.loads(render_sarif(_result(), tmp_path))
    run = payload["runs"][0]

    assert payload["version"] == "2.1.0"
    assert run["tool"]["driver"]["name"] == "RepoInvariant"
    assert run["tool"]["driver"]["rules"][0]["shortDescription"]["text"] == (
        "RepoInvariant finding ENV001"
    )
    assert run["results"][0]["ruleId"] == "ENV001"
    assert run["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 7


def test_sarif_refuses_more_results_than_github_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reporters, "MAX_SARIF_RESULTS", 1)

    with pytest.raises(ValueError, match="limit of 1 results per run"):
        render_sarif(_result(), tmp_path)


def test_sarif_refuses_reports_over_githubs_compressed_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reporters, "MAX_SARIF_COMPRESSED_BYTES", 1)

    with pytest.raises(ValueError, match="10 MiB compressed upload limit"):
        render_sarif(_result(), tmp_path)


def test_warning_threshold_is_reflected_in_all_status_reports(tmp_path: Path) -> None:
    result = ScanResult(
        findings=[Finding("ENV002", "unused", Severity.WARNING)],
        scanned_files={Path(".env.example")},
    )

    assert render_text(result, tmp_path, Severity.WARNING).startswith(
        ".: warning ENV002: unused"
    )
    assert render_text(result, tmp_path, Severity.WARNING).endswith(
        "FAIL: 1 files, 0 errors, 1 warnings\n"
    )
    assert json.loads(render_json(result, tmp_path, Severity.WARNING))["ok"] is False
    assert "❌ Fail" in render_markdown(result, tmp_path, Severity.WARNING)


def test_human_reports_escape_control_characters_and_sarif_quotes_uri(tmp_path: Path) -> None:
    result = ScanResult(
        findings=[
            Finding(
                "ENV001",
                "safe\nmessage",
                Severity.ERROR,
                Location(Path("::warning\nfile #1.yml"), 1, 1),
            )
        ]
    )

    text = render_text(result, tmp_path)
    markdown = render_markdown(result, tmp_path)
    sarif = json.loads(render_sarif(result, tmp_path))

    assert "./::warning\\x0afile #1.yml" in text
    assert "safe\\x0amessage" in text
    assert "&#92;x0a" in markdown
    uri = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"]
    assert uri == "%3A%3Awarning%0Afile%20%231.yml"
