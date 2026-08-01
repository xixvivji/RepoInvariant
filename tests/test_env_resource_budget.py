from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter

import pytest

import repoinvariant.env_contracts as env_contracts

ROOT = Path(__file__).parents[1]


def _config(**sources: list[str]) -> dict[str, list[str]]:
    config = {
        "contracts": [],
        "compose": [],
        "kubernetes": [],
        "workflows": [],
        "spring": [],
    }
    config.update(sources)
    return config


def _set_limits(monkeypatch: pytest.MonkeyPatch, **changes: int) -> None:
    monkeypatch.setattr(
        env_contracts,
        "_ENV_SCAN_LIMITS",
        replace(env_contracts._ENV_SCAN_LIMITS, **changes),
    )


def test_documented_aggregate_limits_match_runtime_contract() -> None:
    limits = env_contracts._ENV_SCAN_LIMITS
    assert (
        limits.max_files,
        limits.max_input_bytes,
        limits.max_items,
        limits.max_findings,
        limits.max_related_locations,
        limits.max_report_bytes,
    ) == (10_000, 64 * 1024 * 1024, 100_000, 10_000, 100, 8 * 1024 * 1024)

    documentation = " ".join(
        (ROOT / "docs/rules/environment-contracts.md")
        .read_text(encoding="utf-8")
        .split()
    )
    for boundary in (
        "10,000 unique files",
        "64 MiB of aggregate UTF-8 input",
        "100,000 parsed occurrences",
        "10,000 findings",
        "8 MiB of encoded finding evidence",
        "100 related locations",
        "exit code `2`",
    ):
        assert boundary in documentation


def test_total_input_budget_is_shared_across_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.env").write_text("A=\n", encoding="utf-8")
    (tmp_path / "b.env").write_text("B=\n", encoding="utf-8")
    _set_limits(monkeypatch, max_input_bytes=5)

    with pytest.raises(ValueError, match="exceeds 5 total input bytes"):
        env_contracts.scan_env_contracts(tmp_path, _config(contracts=["*.env"]))


def test_unique_file_budget_is_shared_across_source_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env.example").write_text("SHARED=\n", encoding="utf-8")
    (tmp_path / "compose.yml").write_text("value: ${SHARED}\n", encoding="utf-8")
    _set_limits(monkeypatch, max_files=1)

    with pytest.raises(ValueError, match="exceeds 1 unique files"):
        env_contracts.scan_env_contracts(
            tmp_path,
            _config(contracts=[".env.example"], compose=["compose.yml"]),
        )


def test_occurrence_budget_fails_before_building_unbounded_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env.example").write_text("A=\nB=\nC=\n", encoding="utf-8")
    _set_limits(monkeypatch, max_items=2)

    with pytest.raises(ValueError, match="exceeds 2 occurrences"):
        env_contracts.scan_env_contracts(
            tmp_path,
            _config(contracts=[".env.example"]),
        )


@pytest.mark.parametrize("yaml_text", ["one\n---\ntwo\n---\nthree\n", "---\n---\n---\n"])
def test_yaml_node_budget_is_shared_across_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    yaml_text: str,
) -> None:
    (tmp_path / "compose.yml").write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(env_contracts, "_MAX_YAML_NODES", 2)

    with pytest.raises(ValueError, match="YAML input exceeds 2 nodes"):
        env_contracts.scan_env_contracts(
            tmp_path,
            _config(compose=["compose.yml"]),
        )


def test_finding_count_and_encoded_payload_have_independent_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contract = tmp_path / ".env.example"
    contract.write_text("A=\nB=\nC=\n", encoding="utf-8")
    _set_limits(monkeypatch, max_findings=2)

    with pytest.raises(ValueError, match="exceeds 2 findings"):
        env_contracts.scan_env_contracts(
            tmp_path,
            _config(contracts=[contract.name]),
        )

    _set_limits(monkeypatch, max_findings=10_000, max_report_bytes=1)
    with pytest.raises(ValueError, match="report exceeds 1 encoded finding bytes"):
        env_contracts.scan_env_contracts(
            tmp_path,
            _config(contracts=[contract.name]),
        )


def test_related_locations_are_deterministically_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "values:\n"
        "  one: ${SHARED}\n"
        "  two: ${SHARED}\n"
        "  three: ${SHARED}\n"
        "  four: ${SHARED}\n"
        "  five: ${SHARED}\n",
        encoding="utf-8",
    )
    _set_limits(monkeypatch, max_related_locations=2)

    first = env_contracts.scan_env_contracts(
        tmp_path,
        _config(compose=[compose.name]),
    )
    second = env_contracts.scan_env_contracts(
        tmp_path,
        _config(compose=[compose.name]),
    )

    finding = first.findings[0]
    assert finding.location is not None
    assert finding.location.line == 2
    assert [location.line for location in finding.related] == [3, 4]
    assert finding.hint is not None
    assert "2 additional locations were omitted" in finding.hint
    assert [item.as_dict() for item in first.findings] == [
        item.as_dict() for item in second.findings
    ]


def test_budget_errors_do_not_expose_environment_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_value = "synthetic-private-canary"
    (tmp_path / ".env.example").write_text(
        f"PRIVATE_INPUT={private_value}\n",
        encoding="utf-8",
    )
    _set_limits(monkeypatch, max_report_bytes=1)

    with pytest.raises(ValueError) as caught:
        env_contracts.scan_env_contracts(
            tmp_path,
            _config(contracts=[".env.example"]),
        )

    assert private_value not in str(caught.value)


@pytest.fixture
def synthetic_large_env_project(tmp_path: Path) -> tuple[Path, dict[str, list[str]], int]:
    count = 5_000
    names = [f"SYNTHETIC_INPUT_{index:05d}" for index in range(count)]
    (tmp_path / ".env.example").write_text(
        "".join(f"{name}=public-placeholder\n" for name in names),
        encoding="utf-8",
    )
    (tmp_path / "compose.yml").write_text(
        "services:\n  app:\n    environment:\n"
        + "".join(f"      {name}: ${{{name}}}\n" for name in names),
        encoding="utf-8",
    )
    return (
        tmp_path,
        _config(contracts=[".env.example"], compose=["compose.yml"]),
        count,
    )


def test_synthetic_large_fixture_stays_within_performance_budget(
    synthetic_large_env_project: tuple[Path, dict[str, list[str]], int],
) -> None:
    root, config, count = synthetic_large_env_project

    started = perf_counter()
    result = env_contracts.scan_env_contracts(root, config)
    elapsed = perf_counter() - started

    assert count == 5_000
    assert not result.findings
    assert result.scanned_files == {Path(".env.example"), Path("compose.yml")}
    # Hosted runners can be briefly CPU-throttled while coverage tracing is active. A ten-second
    # ceiling still catches algorithmic regressions while avoiding failures from sub-second runner
    # variance around the previous five-second boundary.
    assert elapsed < 10.0, f"synthetic 10,000-occurrence scan took {elapsed:.3f}s"
