from __future__ import annotations

from pathlib import Path

from repotruth.env_contracts import scan_env_contracts
from repotruth.models import Severity

FIXTURES = Path(__file__).parent / "fixtures" / "env"


def _names_for(result, code: str) -> set[str]:
    return {finding.message.split("'")[1] for finding in result.findings if finding.code == code}


def _project_config(*, ignore: list[str] | None = None) -> dict[str, object]:
    return {
        "env": {
            "contracts": [".env.example"],
            "compose": ["compose*.yaml"],
            "kubernetes": ["k8s/**/*.yaml"],
            "workflows": [".github/workflows/*.yml"],
            "spring": ["config/application.*"],
            "ignore": ignore or [],
        }
    }


def test_dotenv_contract_supports_export_empty_defaults_and_comments() -> None:
    result = scan_env_contracts(
        FIXTURES / "dotenv",
        {
            "contracts": [".env.example"],
            "compose": [],
            "kubernetes": [],
            "workflows": [],
            "spring": [],
        },
    )

    assert result.error_count == 0
    assert _names_for(result, "ENV002") == {
        "REQUIRED",
        "WITH_DEFAULT",
        "EMPTY",
        "QUOTED_HASH",
    }
    assert {
        finding.message.split("'")[1]: finding.location.line
        for finding in result.findings
        if finding.code == "ENV002" and finding.location is not None
    } == {
        "REQUIRED": 2,
        "WITH_DEFAULT": 3,
        "EMPTY": 4,
        "QUOTED_HASH": 5,
    }
    assert result.scanned_files == {Path(".env.example")}
    assert "super-secret-do-not-print" not in repr(
        [finding.as_dict() for finding in result.findings]
    )


def test_dotenv_unquoted_empty_value_does_not_define_a_default(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        'UNSPECIFIED=\nEXPLICIT_EMPTY=""\n',
        encoding="utf-8",
    )
    (tmp_path / "compose.yml").write_text(
        """services:
  app:
    environment:
      UNSPECIFIED: ${UNSPECIFIED:-runtime-default}
      EXPLICIT_EMPTY: ${EXPLICIT_EMPTY:-runtime-default}
""",
        encoding="utf-8",
    )

    result = scan_env_contracts(
        tmp_path,
        {
            "contracts": [".env.example"],
            "compose": ["compose.yml"],
            "kubernetes": [],
            "workflows": [],
            "spring": [],
        },
    )

    assert _names_for(result, "ENV003") == {"EXPLICIT_EMPTY"}


def test_compose_environment_tracks_sources_and_bare_passthrough_only(tmp_path: Path) -> None:
    (tmp_path / "compose.yml").write_text(
        """services:
  mapping:
    environment:
      MAPPING_TARGET: ${MAPPING_SOURCE:-fallback}
      MAPPING_LITERAL: literal
      MAPPING_BARE:
      MAPPING_SELF: ${MAPPING_SELF}
      MAPPING_REQUIRED: ${MAPPING_REQUIRED_SOURCE?required}
      MAPPING_ALTERNATE: ${MAPPING_ALTERNATE_SOURCE:+replacement}
      MAPPING_PLAIN: $MAPPING_PLAIN_SOURCE
      SINGLE_QUOTED_LITERAL: '$NOT_INTERPOLATED'
  sequence:
    environment:
      - SEQUENCE_TARGET=${SEQUENCE_SOURCE}
      - SEQUENCE_LITERAL=literal
      - SEQUENCE_BARE
""",
        encoding="utf-8",
    )

    result = scan_env_contracts(
        tmp_path,
        {
            "contracts": [],
            "compose": ["compose.yml"],
            "kubernetes": [],
            "workflows": [],
            "spring": [],
        },
    )

    assert _names_for(result, "ENV001") == {
        "MAPPING_BARE",
        "MAPPING_ALTERNATE_SOURCE",
        "MAPPING_PLAIN_SOURCE",
        "MAPPING_REQUIRED_SOURCE",
        "MAPPING_SELF",
        "MAPPING_SOURCE",
        "SEQUENCE_BARE",
        "SEQUENCE_SOURCE",
    }


def test_workflow_tracks_secret_and_variable_references_not_literal_env_keys(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / "workflow.yml"
    workflow.write_text(
        """name: CI
on: push
env:
  LITERAL_KEY: literal
  SECRET_TARGET: ${{ secrets.SECRET_SOURCE }}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo test
        env:
          VARIABLE_TARGET: ${{ vars.VARIABLE_SOURCE }}
          STEP_LITERAL: literal
""",
        encoding="utf-8",
    )

    result = scan_env_contracts(
        tmp_path,
        {
            "contracts": [],
            "compose": [],
            "kubernetes": [],
            "workflows": [workflow.name],
            "spring": [],
        },
    )

    assert _names_for(result, "ENV001") == {"SECRET_SOURCE", "VARIABLE_SOURCE"}


def test_environment_findings_apply_rule_policy(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("UNUSED\n", encoding="utf-8")
    (tmp_path / "compose.yml").write_text(
        "value: ${REQUIRED}\n",
        encoding="utf-8",
    )

    result = scan_env_contracts(
        tmp_path,
        {
            "env": {
                "contracts": [".env.example"],
                "compose": ["compose.yml"],
                "kubernetes": [],
                "workflows": [],
                "spring": [],
                "ignore": [],
            },
            "rules": {"ENV001": "warning", "ENV002": "off"},
        },
    )

    assert [finding.code for finding in result.findings] == ["ENV001"]
    assert result.findings[0].severity is Severity.WARNING


def test_cross_checks_all_supported_environment_consumers() -> None:
    root = FIXTURES / "project"
    result = scan_env_contracts(root, _project_config())

    assert _names_for(result, "ENV001") == {
        "ACTION_VARIABLE",
        "COMPOSE_REQUIRED",
        "ENV_FILE_ONLY",
        "INIT_ONLY",
        "KUBE_MISSING",
        "KUBE_REFERENCE",
        "STEP_SECRET",
    }
    assert "NOT_A_CONSUMER" not in _names_for(result, "ENV001")
    assert _names_for(result, "ENV002") == {"EMPTY", "UNUSED"}
    assert _names_for(result, "ENV003") == {
        "API_URL",
        "DEFAULT_COLON",
        "DEFAULT_DASH",
        "KUBE_MODE",
        "PORT",
    }
    assert result.error_count == 7
    assert result.warning_count == 7
    assert result.scanned_files == {
        Path(".env.example"),
        Path("compose.yaml"),
        Path("runtime.env"),
        Path("k8s/deployment.yaml"),
        Path(".github/workflows/ci.yml"),
        Path("config/application.yml"),
        Path("config/application.properties"),
    }

    port_conflict = next(
        finding
        for finding in result.findings
        if finding.code == "ENV003" and "'PORT'" in finding.message
    )
    assert port_conflict.severity is Severity.WARNING
    assert port_conflict.location is not None
    assert {port_conflict.location.path, *(item.path for item in port_conflict.related)} == {
        Path(".env.example"),
        Path("compose.yaml"),
        Path("config/application.properties"),
    }


def test_ignore_patterns_apply_to_variable_names_and_results_are_deterministic() -> None:
    root = FIXTURES / "project"
    ignored = [
        "ACTION_*",
        "COMPOSE_REQUIRED",
        "ENV_FILE_ONLY",
        "ESCAPED_REFERENCE",
        "*_ONLY",
        "KUBE_MISSING",
        "KUBE_REFERENCE",
        "STEP_*",
        "UNUSED",
    ]

    first = scan_env_contracts(root, _project_config(ignore=ignored))
    second = scan_env_contracts(root, _project_config(ignore=ignored))

    assert not _names_for(first, "ENV001")
    assert _names_for(first, "ENV002") == {"EMPTY"}
    assert _names_for(first, "ENV003") == {
        "API_URL",
        "DEFAULT_COLON",
        "DEFAULT_DASH",
        "KUBE_MODE",
        "PORT",
    }
    assert [finding.as_dict() for finding in first.findings] == [
        finding.as_dict() for finding in second.findings
    ]


def test_dotted_config_keys_and_path_ignores_are_supported() -> None:
    root = FIXTURES / "project"
    result = scan_env_contracts(
        root,
        {
            "env.contracts": [".env.example"],
            "env.compose": ["compose.yaml"],
            "env.kubernetes": [],
            "env.workflows": [],
            "env.spring": [],
            "env.ignore": ["runtime.env"],
        },
    )

    assert Path("runtime.env") not in result.scanned_files
    assert "ENV_FILE_ONLY" not in _names_for(result, "ENV001")
    assert "COMPOSE_REQUIRED" in _names_for(result, "ENV001")
    assert "PORT" in _names_for(result, "ENV003")
