from __future__ import annotations

from pathlib import Path

import pytest

from repoinvariant.models import Severity
from repoinvariant.traceability import scan_traceability

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


def test_prose_mentions_do_not_define_requirements_by_default(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "spec").mkdir()
    (tmp_path / "checks").mkdir()
    (tmp_path / "docs" / "requirements.md").write_text(
        "REQ-PROSE is only mentioned here.\n", encoding="utf-8"
    )
    (tmp_path / "spec" / "api.yaml").write_text(
        "x-feature-id: REQ-PROSE\n", encoding="utf-8"
    )
    (tmp_path / "checks" / "test.txt").write_text("REQ-PROSE\n", encoding="utf-8")

    definitions = scan_traceability(
        tmp_path,
        _config(requirements="docs/*", specifications="spec/*", tests="checks/*"),
    )
    mentions = scan_traceability(
        tmp_path,
        _config(
            requirements="docs/*",
            specifications="spec/*",
            tests="checks/*",
            requirements_mode="mentions",
        ),
    )

    assert [finding.code for finding in definitions.findings] == ["TRACE002"]
    assert mentions.findings == []


def test_rule_policy_can_downgrade_or_disable_findings(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.md").write_text(
        "# REQ-ONE: Missing spec\n", encoding="utf-8"
    )
    config = _config(requirements="docs/*", specifications=[], tests=[])
    config["rules"] = {"TRACE001": "warning"}

    downgraded = scan_traceability(tmp_path, config)
    config["rules"] = {"TRACE001": "off"}
    disabled = scan_traceability(tmp_path, config)

    assert downgraded.findings[0].severity is Severity.WARNING
    assert disabled.findings == []


def test_malformed_specification_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "api.yaml").write_text("paths: [\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid YAML/JSON specification"):
        scan_traceability(tmp_path, _config(requirements=[], specifications="spec/*", tests=[]))


def test_recursive_yaml_alias_does_not_recurse_forever(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "spec").mkdir()
    (tmp_path / "checks").mkdir()
    (tmp_path / "docs" / "requirements.md").write_text(
        "# REQ-ONE: Defined\n", encoding="utf-8"
    )
    (tmp_path / "spec" / "api.yaml").write_text(
        "x-feature-id: &ids [REQ-ONE, *ids]\n", encoding="utf-8"
    )
    (tmp_path / "checks" / "test.txt").write_text("REQ-ONE\n", encoding="utf-8")

    result = scan_traceability(
        tmp_path,
        _config(requirements="docs/*", specifications="spec/*", tests="checks/*"),
    )

    assert result.findings == []


def test_custom_pattern_cannot_emit_secret_shaped_matches(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    secret = "SECRET=do-not-print-this-value"
    (tmp_path / "docs" / "requirements.md").write_text(f"# {secret}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe identifier") as error:
        scan_traceability(
            tmp_path,
            _config(requirements="docs/*", specifications=[], tests=[], id_pattern=r"SECRET=[^ ]+"),
        )

    assert "do-not-print" not in str(error.value)


def test_pathological_custom_pattern_times_out(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "requirements.md").write_text(
        "a" * 100_000 + "!\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="matching timed out"):
        scan_traceability(
            tmp_path,
            _config(requirements="docs/*", specifications=[], tests=[], id_pattern=r"(a+)+$"),
        )


def test_custom_identifiers_are_opaque_in_findings(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    canary = "SYNTHETIC-SECRET-CANARY-7391"
    (tmp_path / "docs" / "requirements.md").write_text(f"# {canary}: Example\n", encoding="utf-8")

    result = scan_traceability(
        tmp_path,
        _config(
            requirements="docs/*",
            specifications=[],
            tests=[],
            id_pattern=r"SYNTHETIC-[A-Z0-9-]+",
        ),
    )

    payload = repr([finding.as_dict() for finding in result.findings])
    assert canary not in payload
    assert "custom-id-1" in payload


def test_matching_budget_is_shared_across_lines(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    attack_line = "a" * 200 + "!\n"
    (tmp_path / "docs" / "requirements.md").write_text(
        attack_line * 1_000, encoding="utf-8"
    )

    with pytest.raises(ValueError, match="matching time budget|matching timed out"):
        scan_traceability(
            tmp_path,
            _config(requirements="docs/*", specifications=[], tests=[], id_pattern=r"(a+)+$"),
        )


def test_invalid_utf8_specification_and_configured_symlink_are_rejected(
    tmp_path: Path,
) -> None:
    (tmp_path / "spec").mkdir()
    invalid = tmp_path / "spec" / "invalid.yaml"
    invalid.write_bytes(b"x-feature-id: \xff\n")

    with pytest.raises(ValueError, match="Invalid UTF-8"):
        scan_traceability(tmp_path, _config(requirements=[], specifications="spec/*", tests=[]))

    invalid.unlink()
    target = tmp_path / "real.yaml"
    target.write_text("x-feature-id: REQ-ONE\n", encoding="utf-8")
    (tmp_path / "spec" / "linked.yaml").symlink_to(target)

    with pytest.raises(ValueError, match="symbolic link"):
        scan_traceability(tmp_path, _config(requirements=[], specifications="spec/*", tests=[]))
