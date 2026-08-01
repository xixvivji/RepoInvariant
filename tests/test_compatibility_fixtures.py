from __future__ import annotations

import hashlib
import json
import re
import shutil
import tomllib
from pathlib import Path
from time import perf_counter

import pytest
import yaml

from repoinvariant.config import ConfigError, load_config
from repoinvariant.detection import detect_config
from repoinvariant.env_contracts import scan_env_contracts
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
UPSTREAM_PROJECTS = {
    "upstream-spring-petclinic": {
        "source": {
            "repository": "https://github.com/spring-projects/spring-petclinic",
            "commit": "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272",
            "license": "Apache-2.0",
            "license_path": "LICENSE.txt",
        },
        "files": {
            "src/main/resources/application.properties": {
                "source_path": "src/main/resources/application.properties",
                "git_blob_sha": "d000edb6a50e07bd7ac2c70f8768d1f776ad3a3b",
                "sha256": "67c1e7e0a0443d290301449110005ab75d223add305ed9c06897437df686de0e",
                "size_bytes": 981,
            },
            "LICENSE.upstream": {
                "source_path": "LICENSE.txt",
                "git_blob_sha": "ff77379631504c17ad0a94dddc6803f0d3fb0fbb",
                "sha256": "56dfc19e0dc836e30177332f73e8e6fbc297941acf3d906eec6eaaa46c2c452a",
                "size_bytes": 11_360,
            },
        },
    },
    "upstream-node-package-esm": {
        "source": {
            "repository": "https://github.com/nodejs/package-examples",
            "commit": "01d632c10d89067a44c4c22b264b2c5a4effce5a",
            "license": "MIT",
            "license_path": "LICENSE",
        },
        "files": {
            "package.json": {
                "source_path": (
                    "guide/02-single-file-package/simple-esm/"
                    "node_modules/my-logger/package.json"
                ),
                "git_blob_sha": "54dcf8497a02cbe6d3dc4ef1b47ac9570b40c93a",
                "sha256": "5f0aa62c05a3416fc6c487b7f2eddb87691208b3e7f87600ec9a0f5ecfadbc29",
                "size_bytes": 151,
            },
            "logger.js": {
                "source_path": (
                    "guide/02-single-file-package/simple-esm/"
                    "node_modules/my-logger/logger.js"
                ),
                "git_blob_sha": "da5c02f91d2a3a29f36faee469ec8b7b967a8f45",
                "sha256": "651badc4953ade6e401caf0e712f516c1f8850e22be8351e64f4d0c6f376c617",
                "size_bytes": 690,
            },
            "LICENSE.upstream": {
                "source_path": "LICENSE",
                "git_blob_sha": "83c1fb9f1d90c4b4859858aa3fbe161ce2440145",
                "sha256": "782ab5b8ae1540c1a2b102fdd00990ed268d4a3a98e90ad7fe724b489d7bb0f2",
                "size_bytes": 1_064,
            },
        },
    },
    "upstream-python-flask": {
        "source": {
            "repository": "https://github.com/pallets/flask",
            "commit": "6a2f545bfd8ed31e19066a299296917e034aca58",
            "license": "BSD-3-Clause",
            "license_path": "LICENSE.txt",
        },
        "files": {
            "pyproject.toml": {
                "source_path": "examples/tutorial/pyproject.toml",
                "git_blob_sha": "9295c5928db201756a1a2f7adf3e3ecaa56a3aa1",
                "sha256": "1b2ff9761cb43059ed8df3f1c27d93cd93c3bf9297fe7fe5716da943a75012ac",
                "size_bytes": 873,
            },
            "LICENSE.upstream": {
                "source_path": "LICENSE.txt",
                "git_blob_sha": "9d227a0cc43c3268d15722b763bd94ad298645a1",
                "sha256": "489a8e1108509ed98a37bb983e11e0f7e1d31f0bd8f99a79c8448e7ff37d07ea",
                "size_bytes": 1_475,
            },
        },
    },
    "upstream-kubernetes-kustomize": {
        "source": {
            "repository": "https://github.com/kubernetes/kubernetes",
            "commit": "a818af18fe29d999d6741234c8cd72709ef2f424",
            "license": "Apache-2.0",
            "license_path": "LICENSE",
        },
        "files": {
            "k8s/deployment.yaml": {
                "source_path": "hack/testdata/kustomize/deployment.yaml",
                "git_blob_sha": "13c096f487fa2185ecae27353dc63c95ba293631",
                "sha256": "738c805201691f8de3dd6c329a2c12cde8707f550c2a99867205f43604bebb82",
                "size_bytes": 782,
            },
            "LICENSE.upstream": {
                "source_path": "LICENSE",
                "git_blob_sha": "d645695673349e3947e8e5ae42332d0ac3164cd7",
                "sha256": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
                "size_bytes": 11_358,
            },
        },
    },
}
SOURCE_LISTS = (
    "gradle",
    "maven",
    "version_files",
    "dockerfiles",
    "compose",
    "workflows",
    "docs",
    "ignore",
    "required",
)
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


def test_pinned_upstream_maven_bytes_have_license_provenance_and_scan() -> None:
    root = FIXTURES / "spring-petclinic-maven"
    manifest = yaml.safe_load((root / "provenance.yml").read_text(encoding="utf-8"))
    copied = manifest["copied_paths"][0]
    snapshot = (root / copied["fixture_path"]).read_bytes()

    assert manifest["kind"] == "pinned-upstream-byte-snapshot"
    assert manifest["copied_bytes"] is True
    assert manifest["source"] == {
        "repository": "https://github.com/spring-projects/spring-petclinic",
        "commit": "88e37c15cf6fc8490b01bc3e8e2c800cec1ac272",
        "license": "Apache-2.0",
    }
    assert copied == {
        "fixture_path": "pom.xml",
        "source_path": "pom.xml",
        "blob_sha": "db5b9f78a5370deae4ace1192d5a8acf4b140d8e",
        "source_lines": {"start": 17, "end": 38},
        "size_bytes": 1_182,
        "sha256": "0bc3e0ba666767dbfb3ca2f3a711cef9a52ff4055da2222fea1c62f1d102674a",
    }
    assert len(snapshot) == copied["size_bytes"]
    assert hashlib.sha256(snapshot).hexdigest() == copied["sha256"]
    assert {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not {".ruff_cache", "__pycache__"}.intersection(path.parts)
    } == {".repoinvariant.yml", "NOTICE", "pom.xml", "provenance.yml"}
    assert (root / "NOTICE").is_file()
    root_license = (root / "../../../../LICENSE").resolve()
    assert root_license.is_file()
    assert "Apache License" in root_license.read_text(encoding="utf-8")

    result = scan_version_contracts(root, load_config(root))
    assert result.findings == []
    assert result.scanned_files == {Path("pom.xml")}


def _git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


@pytest.mark.parametrize("project", sorted(UPSTREAM_PROJECTS))
def test_upstream_syntax_fixture_bytes_are_immutable_and_licensed(project: str) -> None:
    root = FIXTURES / project
    manifest = yaml.safe_load((root / "provenance.yml").read_text(encoding="utf-8"))
    expected = UPSTREAM_PROJECTS[project]

    assert manifest["fixture"] == project
    assert manifest["kind"] == "immutable-upstream-syntax"
    assert manifest["copied_bytes"] is True
    assert manifest["source"] == expected["source"]
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["source"]["commit"])
    assert manifest["observed_at"] == "2026-08-01"
    assert manifest["observation_method"] == "github-contents-api-at-pinned-commit"

    recorded = {item["fixture_path"]: item for item in manifest["files"]}
    assert len(recorded) == len(manifest["files"])
    assert set(recorded) == set(expected["files"])
    for fixture_path, expected_file in expected["files"].items():
        data = (root / fixture_path).read_bytes()
        assert recorded[fixture_path] == {"fixture_path": fixture_path, **expected_file}
        assert len(data) == expected_file["size_bytes"]
        assert hashlib.sha256(data).hexdigest() == expected_file["sha256"]
        assert _git_blob_sha(data) == expected_file["git_blob_sha"]

    assert "LICENSE.upstream" in recorded
    assert (root / "LICENSE.upstream").read_text(encoding="utf-8").strip()
    assert {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not {".ruff_cache", "__pycache__"}.intersection(path.parts)
    } == {"provenance.yml", *recorded}


def test_real_spring_and_kubernetes_syntax_exercises_environment_scanners() -> None:
    spring = FIXTURES / "upstream-spring-petclinic"
    spring_path = "src/main/resources/application.properties"
    spring_config = detect_config(spring)
    spring_result = scan_env_contracts(spring, spring_config)

    assert spring_config["env"]["spring"] == ["src/main/resources/application*.properties"]
    assert spring_result.scanned_files == {Path(spring_path)}
    assert spring_result.findings == []

    kubernetes = FIXTURES / "upstream-kubernetes-kustomize"
    deployment = "k8s/deployment.yaml"
    kubernetes_config = detect_config(kubernetes)
    kubernetes_result = scan_env_contracts(kubernetes, kubernetes_config)

    assert kubernetes_config["env"]["kubernetes"] == ["k8s/**/*.yaml"]
    assert kubernetes_result.scanned_files == {Path(deployment)}
    assert [finding.baseline_key for finding in kubernetes_result.findings] == [
        "ALT_GREETING",
        "ENABLE_RISKY",
    ]
    assert {finding.code for finding in kubernetes_result.findings} == {"ENV001"}


@pytest.mark.parametrize(
    "project",
    ["upstream-node-package-esm", "upstream-python-flask"],
)
def test_node_and_python_fixtures_document_the_unsupported_detection_boundary(
    project: str,
) -> None:
    with pytest.raises(ConfigError, match="no supported repository artifacts"):
        detect_config(FIXTURES / project)


def test_real_node_and_python_package_syntax_is_parseable_offline() -> None:
    node = FIXTURES / "upstream-node-package-esm"
    package = json.loads((node / "package.json").read_text(encoding="utf-8"))
    assert package == {
        "name": "my-logger",
        "version": "1.0.0",
        "type": "module",
        "exports": {".": "./logger.js", "./package.json": "./package.json"},
    }
    assert "export class Logger" in (node / "logger.js").read_text(encoding="utf-8")

    python = FIXTURES / "upstream-python-flask"
    pyproject = tomllib.loads((python / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "flaskr"
    assert pyproject["build-system"]["build-backend"] == "flit_core.buildapi"


def test_upstream_environment_fixture_scans_stay_within_performance_budget() -> None:
    spring = FIXTURES / "upstream-spring-petclinic"
    kubernetes = FIXTURES / "upstream-kubernetes-kustomize"
    spring_config = detect_config(spring)
    kubernetes_config = detect_config(kubernetes)

    started = perf_counter()
    for _ in range(100):
        assert scan_env_contracts(spring, spring_config).findings == []
        assert len(scan_env_contracts(kubernetes, kubernetes_config).findings) == 2
    elapsed = perf_counter() - started

    assert elapsed < 5.0, f"200 pinned upstream fixture scans took {elapsed:.3f}s"


def test_compatibility_fixtures_contain_no_secret_like_test_values() -> None:
    for path in sorted(path for path in FIXTURES.rglob("*") if path.is_file()):
        content = path.read_text(encoding="utf-8")
        assert SECRET_LIKE.search(content) is None, path.relative_to(FIXTURES).as_posix()
