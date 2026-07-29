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


def test_cross_checks_all_supported_environment_consumers() -> None:
    root = FIXTURES / "project"
    result = scan_env_contracts(root, _project_config())

    assert _names_for(result, "ENV001") == {
        "ACTION_LITERAL",
        "ACTION_VARIABLE",
        "COMPOSE_REQUIRED",
        "ENV_FILE_ONLY",
        "ESCAPED_REFERENCE",
        "INIT_ONLY",
        "KUBE_MISSING",
        "KUBE_REFERENCE",
        "STEP_ONLY",
        "STEP_SECRET",
    }
    assert "NOT_A_CONSUMER" not in _names_for(result, "ENV001")
    assert _names_for(result, "ENV002") == {"UNUSED"}
    assert _names_for(result, "ENV003") == {
        "API_URL",
        "DEFAULT_COLON",
        "DEFAULT_DASH",
        "KUBE_MODE",
        "PORT",
    }
    assert result.error_count == 10
    assert result.warning_count == 6
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
    assert not _names_for(first, "ENV002")
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
