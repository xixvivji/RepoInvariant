from __future__ import annotations

from pathlib import Path

import pytest

from repotruth.models import Severity
from repotruth.traceability import scan_traceability

FIXTURES = Path(__file__).parent / "fixtures" / "trace"


def _config(**overrides: object) -> dict[str, object]:
    features: dict[str, object] = {
        "requirements": ["docs/**/*.md"],
        "specifications": ["openapi/**/*.{yaml,yml,json}"],
        "tests": ["checks/**/*.py"],
    }
    features.update(overrides)
    return {"features": features}


def test_complete_trace_has_no_findings_and_tracks_files() -> None:
    root = FIXTURES / "complete"

    result = scan_traceability(root, _config())

    assert result.ok
    assert result.findings == []
    assert result.scanned_files == {
        Path("docs/requirements.md"),
        Path("openapi/api.yaml"),
        Path("checks/test_account.py"),
    }


def test_reports_each_kind_of_traceability_drift_at_source_locations() -> None:
    root = FIXTURES / "drift"

    result = scan_traceability(root, _config())

    assert [(item.code, item.severity) for item in result.findings] == [
        ("TRACE001", Severity.ERROR),
        ("TRACE002", Severity.ERROR),
        ("TRACE003", Severity.ERROR),
        ("TRACE004", Severity.WARNING),
    ]
    by_code = {item.code: item for item in result.findings}
    assert by_code["TRACE001"].location is not None
    assert by_code["TRACE001"].location.path == Path("docs/primary.md")
    assert by_code["TRACE001"].location.line == 3
    assert by_code["TRACE002"].location is not None
    assert by_code["TRACE002"].location.path == Path("openapi/api.yaml")
    assert by_code["TRACE002"].location.line == 6
    assert by_code["TRACE003"].location == by_code["TRACE002"].location
    assert by_code["TRACE004"].location is not None
    assert by_code["TRACE004"].location.path == Path("docs/secondary.md")
    assert by_code["TRACE004"].related[0].path == Path("docs/primary.md")


def test_repeated_mentions_and_markdown_examples_are_not_duplicate_definitions(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "api").mkdir()
    (tmp_path / "checks").mkdir()
    (tmp_path / "docs" / "requirements.md").write_text(
        """# REQ-ONE: Canonical definition

REQ-ONE is discussed in prose.

- [REQ-ONE](#req-one-canonical-definition)

```yaml
x-feature-id: REQ-EXAMPLE
```
""",
        encoding="utf-8",
    )
    (tmp_path / "api" / "openapi.yaml").write_text("x-feature-id: REQ-ONE\n", encoding="utf-8")
    (tmp_path / "checks" / "test_one.py").write_text("# REQ-ONE\n", encoding="utf-8")

    result = scan_traceability(
        tmp_path,
        _config(
            specifications="api/*.yaml",
            requirements="docs/*.md",
            tests="checks/*.py",
        ),
    )

    assert result.findings == []


def test_custom_pattern_extension_and_json_list_are_supported(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "spec").mkdir()
    (tmp_path / "checks").mkdir()
    (tmp_path / "docs" / "features.md").write_text(
        "# FEAT-7: Export\n# FEAT-8: Import\n", encoding="utf-8"
    )
    (tmp_path / "spec" / "api.json").write_text(
        '{"operation": {"x-requirements": ["FEAT-7", "FEAT-8"]}}\n',
        encoding="utf-8",
    )
    (tmp_path / "checks" / "features.txt").write_text("FEAT-7 FEAT-8\n", encoding="utf-8")

    result = scan_traceability(
        tmp_path,
        _config(
            requirements="docs/*.md",
            specifications="spec/*.json",
            tests="checks/*",
            id_pattern=r"FEAT-\d+",
            openapi_extension="x-requirements",
        ),
    )

    assert result.findings == []


def test_ignore_hidden_venv_configured_and_binary_files(tmp_path: Path) -> None:
    for directory in ("docs", "spec", "checks", ".hidden", ".venv", "ignored"):
        (tmp_path / directory).mkdir()
    (tmp_path / "docs" / "requirements.md").write_text("# REQ-OK: Visible\n", encoding="utf-8")
    (tmp_path / "spec" / "api.yaml").write_text("x-feature-id: REQ-OK\n", encoding="utf-8")
    (tmp_path / "checks" / "test.txt").write_text("REQ-OK\n", encoding="utf-8")
    (tmp_path / ".hidden" / "requirements.md").write_text(
        "# REQ-HIDDEN: Hidden\n", encoding="utf-8"
    )
    (tmp_path / ".venv" / "requirements.md").write_text("# REQ-VENV: Hidden\n", encoding="utf-8")
    (tmp_path / "ignored" / "requirements.md").write_text(
        "# REQ-IGNORED: Ignored\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "binary.md").write_bytes(b"REQ-BINARY\x00data")

    result = scan_traceability(
        tmp_path,
        _config(
            requirements="**/*.md",
            specifications="spec/*.yaml",
            tests="checks/*",
            ignore=["ignored/**"],
        ),
    )

    assert result.findings == []
    assert Path("docs/binary.md") not in result.scanned_files
    assert all(
        not any(part.startswith(".") for part in path.parts) for path in result.scanned_files
    )


def test_invalid_id_pattern_has_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid traceability id_pattern"):
        scan_traceability(tmp_path, _config(id_pattern="["))
