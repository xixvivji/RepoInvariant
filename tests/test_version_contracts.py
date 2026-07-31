from __future__ import annotations

from pathlib import Path

import pytest

import repoinvariant.version_contracts as versions
from repoinvariant.models import Severity
from repoinvariant.version_contracts import scan_version_contracts


def _config(
    *,
    expected: str = "21",
    required: list[str] | None = None,
    rules: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "versions": {
            "java": {
                "expected": expected,
                "gradle": ["**/*.gradle", "**/*.gradle.kts"],
                "dockerfiles": ["**/Dockerfile", "**/Dockerfile.*"],
                "compose": ["**/compose*.yml", "**/compose*.yaml"],
                "workflows": [".github/workflows/*.yml", ".github/workflows/*.yaml"],
                "docs": ["README.md", "docs/**/*.md"],
                "ignore": [],
                "required": required or [],
            }
        },
        "rules": rules or {},
    }


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_versions_are_opt_in(tmp_path: Path) -> None:
    _write(tmp_path, "build.gradle.kts", "kotlin { jvmToolchain(17) }\n")

    result = scan_version_contracts(tmp_path, {"rules": {}})

    assert result.findings == []
    assert result.scanned_files == set()


def test_matching_java_major_is_collected_across_all_sources(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "build.gradle.kts",
        """java {
    toolchain.languageVersion.set(JavaLanguageVersion.of(21))
}
kotlin { jvmToolchain(21) }
""",
    )
    _write(
        tmp_path,
        "Dockerfile",
        "ARG JAVA_VERSION=21\nFROM eclipse-temurin:${JAVA_VERSION}-jdk AS build\n",
    )
    _write(
        tmp_path,
        "compose.yaml",
        "services:\n  app:\n    image: amazoncorretto:21-alpine\n",
    )
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        """jobs:
  test:
    steps:
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version: "21.0.2"
""",
    )
    _write(tmp_path, "README.md", "**Java version:** `21`\n")

    result = scan_version_contracts(
        tmp_path,
        _config(required=["gradle", "dockerfiles", "compose", "workflows", "docs"]),
    )

    assert result.findings == []
    assert result.scanned_files == {
        Path("build.gradle.kts"),
        Path("Dockerfile"),
        Path("compose.yaml"),
        Path(".github/workflows/ci.yml"),
        Path("README.md"),
    }


def test_mismatches_are_grouped_by_source_file_with_stable_keys(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "build.gradle.kts",
        "java { toolchain.languageVersion.set(JavaLanguageVersion.of(17)) }\n"
        "kotlin { jvmToolchain(17) }\n",
    )
    _write(tmp_path, "Dockerfile", "FROM gradle:8.14.3-jdk17\n")
    _write(tmp_path, "README.md", "Java version: 17\n")

    result = scan_version_contracts(tmp_path, _config())

    mismatches = [finding for finding in result.findings if finding.code == "VER001"]
    assert len(mismatches) == 3
    assert {finding.location.path for finding in mismatches if finding.location} == {
        Path("build.gradle.kts"),
        Path("Dockerfile"),
        Path("README.md"),
    }
    gradle = next(finding for finding in mismatches if "Gradle" in finding.message)
    assert len(gradle.related) == 1
    assert gradle.baseline_key == "java:mismatch:gradle:build.gradle.kts:17"
    assert "17" in gradle.message
    assert "21" in gradle.message
    assert "baseline_key" not in gradle.as_dict()


def test_dynamic_declarations_warn_without_exposing_values(tmp_path: Path) -> None:
    _write(tmp_path, "build.gradle.kts", "kotlin { jvmToolchain(javaTarget.get()) }\n")
    _write(tmp_path, "Dockerfile", "ARG JAVA_VERSION\nFROM openjdk:${JAVA_VERSION}-jdk\n")
    _write(
        tmp_path,
        "compose.yaml",
        "services:\n  app:\n    image: eclipse-temurin:${JAVA_VERSION}-jdk\n",
    )
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        """jobs:
  test:
    steps:
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version: ${{ matrix.runtime_secret }}
""",
    )
    _write(tmp_path, "README.md", "Java version: ${PRIVATE_RUNTIME_VALUE}\n")

    result = scan_version_contracts(tmp_path, _config())

    dynamic = [finding for finding in result.findings if finding.code == "VER002"]
    assert len(dynamic) == 5
    rendered = repr([finding.as_dict() for finding in dynamic])
    assert "runtime_secret" not in rendered
    assert "PRIVATE_RUNTIME_VALUE" not in rendered
    assert "JAVA_VERSION" not in rendered


def test_required_sources_report_only_sources_without_declarations(tmp_path: Path) -> None:
    _write(tmp_path, "build.gradle.kts", "kotlin { jvmToolchain(versionProvider) }\n")
    _write(tmp_path, "README.md", "This project runs on a supported Java release.\n")

    result = scan_version_contracts(
        tmp_path,
        _config(required=["gradle", "dockerfiles", "compose", "workflows", "docs"]),
    )

    missing_sources = {
        finding.message.split("'")[1]
        for finding in result.findings
        if finding.code == "VER003"
    }
    assert missing_sources == {
        "Dockerfile",
        "Compose",
        "GitHub Actions",
        "documentation",
    }
    assert any(finding.code == "VER002" for finding in result.findings)


def test_setup_java_version_file_is_bounded_and_reported_at_the_file(tmp_path: Path) -> None:
    _write(tmp_path, ".java-version", "temurin-17.0.12+7\n")
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        """jobs:
  test:
    steps:
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version-file: .java-version
""",
    )

    result = scan_version_contracts(tmp_path, _config(required=["workflows"]))

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.code == "VER001"
    assert finding.location is not None
    assert finding.location.path == Path(".java-version")
    assert Path(".java-version") in result.scanned_files


def test_tool_versions_java_entry_is_supported(tmp_path: Path) -> None:
    _write(tmp_path, ".tool-versions", "nodejs 22.1.0\njava temurin-21.0.5+11\n")
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        """jobs:
  test:
    steps:
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version-file: .tool-versions
""",
    )

    result = scan_version_contracts(tmp_path, _config(required=["workflows"]))

    assert result.findings == []
    assert Path(".tool-versions") in result.scanned_files


def test_repeated_version_file_references_are_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, ".java-version", "21\n")
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        """jobs:
  test:
    steps:
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version-file: .java-version
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version-file: .java-version
""",
    )
    reads = 0
    original = versions._read_scan_text

    def counted(root: Path, path: Path) -> str:
        nonlocal reads
        if path.name == ".java-version":
            reads += 1
        return original(root, path)

    monkeypatch.setattr(versions, "_read_scan_text", counted)

    result = scan_version_contracts(tmp_path, _config(required=["workflows"]))

    assert result.findings == []
    assert reads == 1


def test_malformed_configured_yaml_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "compose.yaml", "services:\n  app: [\n")

    with pytest.raises(ValueError, match="invalid YAML"):
        scan_version_contracts(tmp_path, _config())


def test_version_file_cannot_escape_or_follow_a_symlink(tmp_path: Path) -> None:
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        """jobs:
  test:
    steps:
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version-file: ../outside-version
""",
    )

    with pytest.raises(ValueError, match="inside the repository"):
        scan_version_contracts(tmp_path, _config())


def test_comments_fences_and_unstructured_prose_are_not_declarations(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "build.gradle.kts",
        "// jvmToolchain(17)\n"
        "/* JavaLanguageVersion.of(17) */\n"
        'val example = "jvmToolchain(17)"\n'
        'val multiline = """JavaLanguageVersion.of(17)"""\n'
        "def slashy = /jvmToolchain(17)/\n"
        "def returned() { return /jvmToolchain(17)/ }\n"
        "patterns << /jvmToolchain(17)/\n"
        "def dollarSlashy = $/JavaLanguageVersion.of(17)/$\n"
        "def unfinished = $/jvmToolchain(17)\n",
    )
    _write(
        tmp_path,
        "README.md",
        "We recommend upgrading from Java 17.\n"
        "```text\n~~~\nJava version: 17\n~~~\n```\n",
    )

    result = scan_version_contracts(tmp_path, _config(required=["gradle", "docs"]))

    assert not any(finding.code in {"VER001", "VER002"} for finding in result.findings)
    assert {finding.code for finding in result.findings} == {"VER003"}


def test_quoted_gradle_major_is_supported_but_bytecode_target_is_not_a_toolchain(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "build.gradle.kts",
        'kotlin { jvmToolchain("21") }\n'
        "sourceCompatibility = JavaVersion.VERSION_17\n"
        "targetCompatibility = JavaVersion.VERSION_17\n",
    )

    result = scan_version_contracts(tmp_path, _config(required=["gradle"]))

    assert result.findings == []


def test_multiline_gradle_toolchain_is_not_skipped(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "build.gradle.kts",
        "kotlin {\n  jvmToolchain(\n    17\n  )\n}\n",
    )

    result = scan_version_contracts(tmp_path, _config(required=["gradle"]))

    assert [finding.code for finding in result.findings] == ["VER001"]
    assert result.findings[0].location is not None
    assert (result.findings[0].location.line, result.findings[0].location.column) == (3, 5)


def test_gradle_declaration_bound_stops_parsing_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "build.gradle.kts",
        "".join("kotlin { jvmToolchain(21) }\n" for _ in range(100)),
    )
    calls = 0
    original = versions._major_from_literal

    def counted(value: str) -> str | None:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(versions, "_MAX_DECLARATIONS_PER_FILE", 1)
    monkeypatch.setattr(versions, "_major_from_literal", counted)

    with pytest.raises(ValueError, match="in one file"):
        scan_version_contracts(tmp_path, _config())

    assert calls == 2


def test_structured_docs_support_vendor_text_and_reject_ranges_and_html_comments(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "README.md",
        "<!--\nJava version: 17\n-->\n"
        "| Component | Version |\n"
        "|---|---|\n"
        "| Java | Eclipse Temurin `21.0.11+10` |\n",
    )

    clean = scan_version_contracts(tmp_path, _config(required=["docs"]))
    assert clean.findings == []

    _write(tmp_path, "README.md", "Java version: 17-21\n")
    dynamic = scan_version_contracts(tmp_path, _config(required=["docs"]))
    assert [finding.code for finding in dynamic.findings] == ["VER002"]

    for declaration in ("Java version: 21 or later\n", "Java version: 21+\n"):
        _write(tmp_path, "README.md", declaration)
        dynamic = scan_version_contracts(tmp_path, _config(required=["docs"]))
        assert [finding.code for finding in dynamic.findings] == ["VER002"]


def test_legacy_java_literal_normalizes_to_its_feature_major(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "Java version: 1.8.0\n")
    _write(tmp_path, "Dockerfile", "FROM openjdk:1.8.0_402\n")

    result = scan_version_contracts(tmp_path, _config(expected="8"))

    assert result.findings == []


def test_compose_merge_inherits_a_java_image_declaration(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "compose.yaml",
        """x-java: &java
  image: eclipse-temurin:17
services:
  app:
    <<: *java
""",
    )

    result = scan_version_contracts(tmp_path, _config(required=["compose"]))

    assert [finding.code for finding in result.findings] == ["VER001"]


def test_non_java_images_and_gradle_stage_arguments_do_not_create_false_evidence(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "Dockerfile",
        "FROM postgres:17.6\nARG JAVA_VERSION=17\n"
        "FROM eclipse-temurin:${JAVA_VERSION}-jdk\n",
    )
    _write(
        tmp_path,
        "compose.yaml",
        "services:\n  database:\n    image: postgres:17.6\n  app:\n    image: acme/app:21\n",
    )

    result = scan_version_contracts(
        tmp_path,
        _config(required=["dockerfiles", "compose"]),
    )

    assert {finding.code for finding in result.findings} == {"VER002", "VER003"}
    dynamic = next(finding for finding in result.findings if finding.code == "VER002")
    assert dynamic.location is not None
    assert dynamic.location.path == Path("Dockerfile")


def test_docker_arg_substitution_is_bounded_before_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(versions, "_MAX_DOCKER_IMAGE_CHARS", 48)
    _write(
        tmp_path,
        "Dockerfile",
        "ARG JAVA_TAG=12345678901234567890\n"
        "FROM eclipse-temurin:$JAVA_TAG$JAVA_TAG\n",
    )

    with pytest.raises(ValueError, match="image expansion exceeds"):
        scan_version_contracts(tmp_path, _config())


def test_rule_policy_and_ignore_patterns_are_applied_deterministically(tmp_path: Path) -> None:
    _write(tmp_path, "build.gradle.kts", "kotlin { jvmToolchain(17) }\n")
    _write(tmp_path, "examples/ignored.gradle.kts", "kotlin { jvmToolchain(11) }\n")
    config = _config(rules={"VER001": "warning", "VER003": "off"})
    java = config["versions"]["java"]
    java["ignore"] = ["examples/**"]
    java["required"] = ["docs"]

    first = scan_version_contracts(tmp_path, config)
    second = scan_version_contracts(tmp_path, config)

    assert len(first.findings) == 1
    assert first.findings[0].severity is Severity.WARNING
    assert first.findings[0].location is not None
    assert first.findings[0].location.path == Path("build.gradle.kts")
    assert [finding.as_dict() for finding in first.findings] == [
        finding.as_dict() for finding in second.findings
    ]


def test_invalid_expected_major_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="canonical Java major"):
        scan_version_contracts(tmp_path, _config(expected="021"))


def test_explicit_null_versions_fails_closed_for_library_callers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="versions.java.*mapping"):
        scan_version_contracts(tmp_path, {"versions": None})


def test_version_file_rejects_a_symlinked_parent(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    _write(real, ".java-version", "21\n")
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)
    _write(
        tmp_path,
        ".github/workflows/ci.yml",
        """jobs:
  test:
    steps:
      - uses: actions/setup-java@0123456789012345678901234567890123456789
        with:
          java-version-file: linked/.java-version
""",
    )

    with pytest.raises(ValueError, match="symbolic links"):
        scan_version_contracts(tmp_path, _config())


def test_duplicate_yaml_keys_and_declaration_bounds_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "compose.yaml",
        "services:\n  app:\n    image: eclipse-temurin:21\n"
        "    image: eclipse-temurin:17\n",
    )
    with pytest.raises(ValueError, match="duplicate YAML"):
        scan_version_contracts(tmp_path, _config())

    (tmp_path / "compose.yaml").unlink()
    _write(
        tmp_path,
        "build.gradle.kts",
        "kotlin { jvmToolchain(21) }\nkotlin { jvmToolchain(21) }\n",
    )
    monkeypatch.setattr(versions, "_MAX_DECLARATIONS_PER_FILE", 1)
    with pytest.raises(ValueError, match="in one file"):
        scan_version_contracts(tmp_path, _config())


def test_repeated_yaml_alias_references_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "compose.yaml",
        "x-java: &java\n  image: eclipse-temurin:21\nservices:\n"
        + "".join(f"  app-{index}: *java\n" for index in range(20)),
    )
    monkeypatch.setattr(versions, "_MAX_YAML_NODES", 30)

    with pytest.raises(ValueError, match="nodes and references"):
        scan_version_contracts(tmp_path, _config())


def test_required_source_without_evidence_points_to_first_candidate(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "No structured declaration here.\n")

    result = scan_version_contracts(tmp_path, _config(required=["docs"]))

    assert len(result.findings) == 1
    assert result.findings[0].code == "VER003"
    assert result.findings[0].location is not None
    assert result.findings[0].location.path == Path("README.md")


def test_longer_markdown_fence_is_not_closed_by_a_shorter_nested_fence(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "README.md",
        "````text\n```\nJava version: 17\n```\n````\n",
    )

    result = scan_version_contracts(tmp_path, _config(required=["docs"]))

    assert [finding.code for finding in result.findings] == ["VER003"]


def test_markdown_fence_info_is_not_a_closer_and_comments_inside_stay_local(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "README.md",
        "```text\nJava version: 17\n```yaml\nJava version: 17\n```\n"
        "```text\n<!--\n```\nJava version: 17\n",
    )

    result = scan_version_contracts(tmp_path, _config(required=["docs"]))

    assert [finding.code for finding in result.findings] == ["VER001"]
    assert result.findings[0].location is not None
    assert result.findings[0].location.line == 9
