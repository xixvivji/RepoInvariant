from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from repoinvariant.config import load_config
from repoinvariant.version_contracts import scan_version_contracts

FIXTURES = Path(__file__).parent / "fixtures" / "compatibility"
PROJECTS = {
    "spring-guide-gradle": {
        "source": {
            "repository": "https://github.com/spring-guides/gs-spring-boot-docker",
            "commit": "8f42f5812e8b62bc31b092a8767a5073bbc786e0",
            "license": "Apache-2.0",
        },
        "observed": {
            "README.adoc": "4df8fc226a9d09df01284d433ce56777b1dc16d2",
            "complete/build.gradle": "24c9f9f24cbe7c3007c3357cbd5e51f07a8a18a3",
            "complete/Dockerfile": "509e98b69f819409adfea404348a2a31df842d14",
        },
    },
    "fineract-multimodule": {
        "source": {
            "repository": "https://github.com/apache/fineract",
            "commit": "c8b48ee8da3aaa135f7d327bf4e09bfa917e8c13",
            "license": "Apache-2.0",
        },
        "observed": {
            "README.md": "3aa136a9b6136ac9b7b80f2d8a0a960750f7dcf6",
            "build.gradle": "19eeb7f2ab73fab56ed1bd809db0b6cff1836ebd",
            "fineract-core/build.gradle": "1a7d533afae4d8f54f2b6e4b4d7fee337afcbb25",
            "docker-compose.yml": "c89fabf4e3ce9961be220445f2827f5dfb7d3a4c",
            ".github/workflows/build-quality-checks.yml": (
                "cead5937de0d17188fe45ee66ed895e9526b1e09"
            ),
        },
    },
    "testcontainers-library": {
        "source": {
            "repository": "https://github.com/testcontainers/testcontainers-java",
            "commit": "2ac3c9773ca52381266463c3709c37155e190b68",
            "license": "MIT",
        },
        "observed": {
            "README.md": "26f45f451e1e095f29c9db5e90b6dcf657390d8b",
            "build.gradle": "f405ea2b92cc2927eb1725faafbd8f0b773abc83",
            "core/build.gradle": "66f9fe7dcc004c4186ae007631a5154f25c33c97",
            "docker-compose.yml": "bfb6bdc35fe19709c3fd91bf9fccfc035f1c34c0",
            ".github/workflows/ci.yml": "a3b19e818237abd41f0b47e82d6dbabd2a774c66",
        },
    },
}
SOURCE_LISTS = ("gradle", "dockerfiles", "compose", "workflows", "docs", "ignore", "required")
SECRET_LIKE = re.compile(
    r"(?i)\b(?:api[-_ ]?key|access[-_ ]?token|password|passwd|client[-_ ]?secret|"
    r"private[-_ ]?key)\b|\$\{\{\s*secrets\.",
)


@pytest.mark.parametrize("project", sorted(PROJECTS))
def test_compatibility_fixture_provenance_is_pinned_and_synthetic(project: str) -> None:
    manifest = yaml.safe_load((FIXTURES / project / "provenance.yml").read_text(encoding="utf-8"))
    expected = PROJECTS[project]

    assert manifest["fixture"] == project
    assert manifest["kind"] == "pinned-structural-adaptation"
    assert manifest["copied_bytes"] is False
    assert manifest["source"] == expected["source"]
    assert manifest["source"]["repository"].startswith("https://github.com/")
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["source"]["commit"])
    assert manifest["observed_at"] == "2026-07-30"
    assert manifest["observation_method"] == "github-contents-api-at-pinned-commit"
    assert manifest["derivation"] == "independently-authored-structural-adaptation"

    observed = {item["path"]: item["blob_sha"] for item in manifest["observed_paths"]}
    assert observed == expected["observed"]
    assert all(re.fullmatch(r"[0-9a-f]{40}", blob) for blob in observed.values())

    adapted = manifest["adapted_paths"]
    assert adapted
    for item in adapted:
        assert (FIXTURES / project / item["fixture_path"]).is_file()
        if "observed_from" in item:
            assert item["observed_from"] in observed
        else:
            assert item["synthetic_extension"] is True


@pytest.mark.parametrize("project", sorted(PROJECTS))
def test_compatibility_fixture_has_complete_java_contract(project: str) -> None:
    root = FIXTURES / project
    config = load_config(root)
    java = config["versions"]["java"]

    assert java["expected"] == "21"
    assert all(key in java for key in SOURCE_LISTS)
    assert java["required"] == ["gradle", "dockerfiles", "workflows", "docs"]

    result = scan_version_contracts(root, config)

    assert result.findings == []


def test_compatibility_fixture_detects_a_version_mutation(tmp_path: Path) -> None:
    source = FIXTURES / "spring-guide-gradle"
    root = tmp_path / source.name
    shutil.copytree(source, root)
    build_file = root / "build.gradle"
    build_file.write_text(
        build_file.read_text(encoding="utf-8").replace(
            "JavaLanguageVersion.of(21)",
            "JavaLanguageVersion.of(17)",
        ),
        encoding="utf-8",
    )

    result = scan_version_contracts(root, load_config(root))

    assert "VER001" in {finding.code for finding in result.findings}


def test_compatibility_fixtures_contain_no_secret_like_test_values() -> None:
    for path in sorted(path for path in FIXTURES.rglob("*") if path.is_file()):
        content = path.read_text(encoding="utf-8")
        assert SECRET_LIKE.search(content) is None, path.relative_to(FIXTURES).as_posix()
