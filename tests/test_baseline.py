import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from repoinvariant.baseline import (
    FINGERPRINT_VERSION,
    MAX_BASELINE_BYTES,
    MAX_BASELINE_FINDINGS,
    BaselineError,
    apply_baseline,
    compute_scope_digest,
    create_baseline,
    finding_fingerprint,
    load_baseline,
    render_baseline,
)
from repoinvariant.config import DEFAULT_CONFIG, VERSION_JAVA_DEFAULTS
from repoinvariant.models import Finding, Location, ScanResult, Severity


def _finding(
    code: str,
    key: str | None,
    *,
    severity: Severity = Severity.ERROR,
    message: str = "synthetic drift",
    path: str = "config/example.yml",
) -> Finding:
    return Finding(
        code=code,
        message=message,
        severity=severity,
        location=Location(Path(path), 7, 3),
        baseline_key=key,
    )


def _baseline_document(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    baseline = create_baseline(
        ScanResult(findings=[_finding("ENV001", "SERVICE_PORT")]),
        DEFAULT_CONFIG,
        tool_version="1.2.3",
    )
    path = tmp_path / ".repoinvariant-baseline.json"
    document = json.loads(render_baseline(baseline))
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


def test_fingerprint_is_canonical_v1_sha256() -> None:
    finding = _finding("ENV001", "SERVICE_PORT")
    canonical = b'["repoinvariant-finding",1,"ENV001","SERVICE_PORT"]'

    assert FINGERPRINT_VERSION == 1
    assert finding_fingerprint(finding) == f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def test_internal_baseline_key_does_not_change_public_finding_behavior() -> None:
    first = _finding("ENV001", "FIRST")
    second = _finding("ENV001", "SECOND")

    assert first == second
    assert "baseline_key" not in repr(first)
    assert Finding.__match_args__ == (
        "code",
        "message",
        "severity",
        "location",
        "hint",
        "related",
    )


def test_baseline_render_is_deterministic_sorted_and_private() -> None:
    first = _finding(
        "TRACE003",
        "private-custom-id",
        message="do not retain this private message",
        path="private/location.yml",
    )
    second = _finding(
        "ENV002",
        "SERVICE_TOKEN",
        severity=Severity.WARNING,
        message="another private message",
        path="secrets/example.env",
    )

    rendered = render_baseline(
        create_baseline(
            ScanResult(findings=[first, second]),
            DEFAULT_CONFIG,
            tool_version="1.2.3",
        )
    )
    reversed_rendered = render_baseline(
        create_baseline(
            ScanResult(findings=[second, first]),
            deepcopy(DEFAULT_CONFIG),
            tool_version="1.2.3",
        )
    )

    assert rendered == reversed_rendered
    assert [entry["code"] for entry in json.loads(rendered)["findings"]] == [
        "ENV002",
        "TRACE003",
    ]
    for sensitive in (
        "private-custom-id",
        "SERVICE_TOKEN",
        "private message",
        "private/location.yml",
        "secrets/example.env",
    ):
        assert sensitive not in rendered


def test_scope_digest_uses_effective_defaults_sorted_keys_and_list_order() -> None:
    reordered = {key: deepcopy(DEFAULT_CONFIG[key]) for key in reversed(DEFAULT_CONFIG)}

    assert compute_scope_digest({"version": 1}) == compute_scope_digest(reordered)

    changed_order = deepcopy(DEFAULT_CONFIG)
    changed_order["env"]["compose"] = list(reversed(changed_order["env"]["compose"]))
    assert compute_scope_digest(changed_order) != compute_scope_digest(DEFAULT_CONFIG)
    assert compute_scope_digest(DEFAULT_CONFIG, no_env=True) != compute_scope_digest(
        DEFAULT_CONFIG
    )
    assert compute_scope_digest(DEFAULT_CONFIG, no_features=True) != compute_scope_digest(
        DEFAULT_CONFIG
    )


def test_scope_digest_preserves_v03_scope_and_tracks_opt_in_versions() -> None:
    assert compute_scope_digest(DEFAULT_CONFIG) == (
        "sha256:a53bae2a651f98adf93e1fa1f5dfe4b1e94f42d4406e25920e1717cdd93a92be"
    )
    assert compute_scope_digest(DEFAULT_CONFIG, no_features=True) == (
        "sha256:2e0c978d3c946f8a4eaa560b86e6292bbc5acd28c5f9870af96f6813cc5224d8"
    )
    assert compute_scope_digest(DEFAULT_CONFIG, no_versions=True) == compute_scope_digest(
        DEFAULT_CONFIG
    )
    assert compute_scope_digest(DEFAULT_CONFIG, no_env=True) == (
        "sha256:59955fb463dbd216862ae45b0dd8998e529604aa37482d660b62a0f9bcdd26bc"
    )
    assert compute_scope_digest(DEFAULT_CONFIG, no_env=True, no_features=True) == (
        "sha256:5603b2603cebde00011a9f98e5deb94b82c16a5e2d0f56aafa16f4f18343ea40"
    )
    version_config = {
        "version": 1,
        "versions": {"java": {"expected": "21"}},
    }

    enabled = compute_scope_digest(version_config)
    expanded_version_config = {
        "version": 1,
        "versions": {
            "java": {
                **deepcopy(VERSION_JAVA_DEFAULTS),
                "expected": "21",
            }
        },
    }

    assert enabled != compute_scope_digest(DEFAULT_CONFIG)
    assert enabled == compute_scope_digest(expanded_version_config)
    assert enabled != compute_scope_digest(version_config, no_versions=True)
    changed = deepcopy(version_config)
    changed["versions"]["java"]["expected"] = "17"
    assert compute_scope_digest(changed) != enabled


def test_create_and_apply_baseline_preserve_new_warning_error_and_input() -> None:
    accepted_error = _finding("ENV001", "ACCEPTED")
    accepted_warning = _finding("ENV002", "OLD_WARNING", severity=Severity.WARNING)
    stale = _finding("TRACE003", "REMOVED")
    baseline = create_baseline(
        ScanResult(findings=[accepted_warning, stale, accepted_error]),
        DEFAULT_CONFIG,
    )
    new_error = _finding("TRACE001", "NEW_ERROR")
    new_warning = _finding("TRACE004", "NEW_WARNING", severity=Severity.WARNING)
    source = ScanResult(
        findings=[new_warning, accepted_warning, new_error, accepted_error],
        scanned_files={Path("docs/requirements.md")},
    )
    original_findings = list(source.findings)

    application = apply_baseline(source, baseline, DEFAULT_CONFIG)

    assert application.result.findings == [new_warning, new_error]
    assert application.result.scanned_files == source.scanned_files
    assert application.result.scanned_files is not source.scanned_files
    assert application.suppressed_count == 2
    assert application.stale_count == 1
    assert source.findings == original_findings


def test_apply_rejects_scope_mismatch() -> None:
    baseline = create_baseline(
        ScanResult(findings=[_finding("ENV001", "SERVICE_PORT")]),
        DEFAULT_CONFIG,
    )

    with pytest.raises(BaselineError, match="scope does not match"):
        apply_baseline(ScanResult(), baseline, DEFAULT_CONFIG, no_env=True)


def test_keyless_finding_cannot_be_created_or_suppressed() -> None:
    keyless = _finding("ENV001", None)
    with pytest.raises(BaselineError, match="no stable baseline key"):
        create_baseline(ScanResult(findings=[keyless]), DEFAULT_CONFIG)

    baseline = create_baseline(
        ScanResult(findings=[_finding("ENV001", "MATCHABLE")]),
        DEFAULT_CONFIG,
    )
    application = apply_baseline(ScanResult(findings=[keyless]), baseline, DEFAULT_CONFIG)

    assert application.result.findings == [keyless]
    assert application.suppressed_count == 0
    assert application.stale_count == 1
    assert finding_fingerprint(keyless) is None


@pytest.mark.parametrize("operation", ["create", "apply"])
def test_duplicate_current_fingerprint_fails_closed(operation: str) -> None:
    first = _finding("ENV001", "DUPLICATE")
    duplicate = _finding("ENV001", "DUPLICATE", message="a second occurrence")
    result = ScanResult(findings=[first, duplicate])

    with pytest.raises(BaselineError, match="duplicate .*fingerprint"):
        if operation == "create":
            create_baseline(result, DEFAULT_CONFIG)
        else:
            baseline = create_baseline(ScanResult(findings=[first]), DEFAULT_CONFIG)
            apply_baseline(result, baseline, DEFAULT_CONFIG)


def test_load_round_trips_valid_baseline(tmp_path: Path) -> None:
    path, _ = _baseline_document(tmp_path)

    loaded = load_baseline(tmp_path, path)

    assert render_baseline(loaded) == render_baseline(
        create_baseline(
            ScanResult(findings=[_finding("ENV001", "SERVICE_PORT")]),
            DEFAULT_CONFIG,
            tool_version="1.2.3",
        )
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda doc: doc.update({"unexpected": True}), "unknown key"),
        (lambda doc: doc.update({"schema_version": True}), "must be an integer"),
        (lambda doc: doc.update({"schema_version": 2}), "schema version 1"),
        (lambda doc: doc.update({"fingerprint_version": False}), "must be an integer"),
        (lambda doc: doc.update({"fingerprint_version": 2}), "fingerprint version 1"),
        (lambda doc: doc["tool"].update({"extra": "x"}), "unknown key"),
        (lambda doc: doc["tool"].update({"name": "another-tool"}), "tool name"),
        (lambda doc: doc["tool"].update({"version": "bad version"}), "tool version"),
        (lambda doc: doc.update({"scope_digest": "not-a-digest"}), "scope digest"),
        (lambda doc: doc.update({"findings": {}}), "JSON array"),
        (lambda doc: doc["findings"][0].update({"extra": "x"}), "unknown key"),
        (lambda doc: doc["findings"][0].update({"severity": "critical"}), "severity"),
        (lambda doc: doc["findings"][0].update({"severity": 1}), "must be strings"),
        (lambda doc: doc["findings"][0].update({"code": "bad-code"}), "code is invalid"),
        (lambda doc: doc["findings"][0].update({"fingerprint": "f"}), "fingerprint"),
    ],
)
def test_load_rejects_unknown_keys_types_and_invalid_values(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    path, document = _baseline_document(tmp_path)
    mutation(document)  # type: ignore[operator]
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(BaselineError, match=message):
        load_baseline(tmp_path, path)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_load_rejects_non_finite_json_numbers(tmp_path: Path, constant: str) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(f'{{"value": {constant}}}', encoding="utf-8")

    with pytest.raises(BaselineError, match="non-finite"):
        load_baseline(tmp_path, path)


def test_load_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(BaselineError, match="duplicate JSON key"):
        load_baseline(tmp_path, path)


def test_load_escapes_untrusted_unknown_keys_in_errors(tmp_path: Path) -> None:
    path, document = _baseline_document(tmp_path)
    document["unexpected\n::error title=spoofed::message"] = True
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(BaselineError) as captured:
        load_baseline(tmp_path, path)

    message = str(captured.value)
    assert "\n::error" not in message
    assert r"\n::error title=spoofed::message" in message


def test_load_rejects_duplicate_fingerprints(tmp_path: Path) -> None:
    path, document = _baseline_document(tmp_path)
    document["findings"].append(dict(document["findings"][0]))
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(BaselineError, match="duplicate baseline fingerprint"):
        load_baseline(tmp_path, path)


def test_load_rejects_unsorted_findings(tmp_path: Path) -> None:
    baseline = create_baseline(
        ScanResult(
            findings=[
                _finding("ENV001", "A"),
                _finding("TRACE003", "B"),
            ]
        ),
        DEFAULT_CONFIG,
    )
    document = json.loads(render_baseline(baseline))
    document["findings"].reverse()
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(BaselineError, match="must be sorted"):
        load_baseline(tmp_path, path)


def test_load_rejects_more_than_ten_thousand_findings(tmp_path: Path) -> None:
    path, document = _baseline_document(tmp_path)
    document["findings"] = [document["findings"][0]] * (MAX_BASELINE_FINDINGS + 1)
    path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(BaselineError, match="10000 findings"):
        load_baseline(tmp_path, path)


def test_load_rejects_oversize_and_invalid_utf8_files(tmp_path: Path) -> None:
    oversize = tmp_path / "oversize.json"
    oversize.write_bytes(b" " * (MAX_BASELINE_BYTES + 1))
    with pytest.raises(BaselineError, match="exceeds"):
        load_baseline(tmp_path, oversize)

    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"\xff")
    with pytest.raises(BaselineError, match="cannot read baseline"):
        load_baseline(tmp_path, invalid)


def test_load_wraps_parser_recursion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "recursive.json"
    path.write_text("{}", encoding="utf-8")

    def recurse(*args: object, **kwargs: object) -> object:
        raise RecursionError("synthetic parser recursion")

    monkeypatch.setattr("repoinvariant.baseline.json.loads", recurse)

    with pytest.raises(BaselineError, match="cannot read baseline"):
        load_baseline(tmp_path, path)


def test_load_rejects_symlink_outside_and_outside_absolute_path(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = root / "baseline.json"
    link.symlink_to(outside)

    with pytest.raises(BaselineError, match="symbolic link"):
        load_baseline(root, link)
    with pytest.raises(BaselineError, match="inside repository"):
        load_baseline(root, outside)


def test_load_rejects_symlinked_parent_even_when_target_stays_inside(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    path, _ = _baseline_document(real_parent)
    (tmp_path / "linked").symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(BaselineError, match="symbolic links"):
        load_baseline(tmp_path, Path("linked") / path.name)


def test_load_rejects_non_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "baseline.json"
    directory.mkdir()

    with pytest.raises(BaselineError, match="not a regular file"):
        load_baseline(tmp_path, directory)
