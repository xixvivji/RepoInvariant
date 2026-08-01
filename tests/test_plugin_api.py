from __future__ import annotations

import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from types import ModuleType

import pytest

import repoinvariant.plugin_api as plugin_api
from repoinvariant.filesystem import MAX_SCAN_BYTES
from repoinvariant.models import Location, Severity
from repoinvariant.plugin_api import (
    ENTRY_POINT_GROUP,
    MAX_PLUGIN_FINDINGS,
    LoadedPlugin,
    PluginEntryPoint,
    PluginError,
    PluginFinding,
    PluginLocation,
    PluginRule,
    RepositoryView,
    discover_plugin_entry_points,
    load_plugins,
    plugin_scope_payload,
    scan_plugins,
)

FIXTURE_SITE = Path(__file__).parent / "fixtures" / "plugins" / "site-packages"


class _Scanner:
    api_version = 1

    def __init__(
        self,
        plugin_id: str,
        *,
        findings: tuple[PluginFinding, ...] = (),
        rules: tuple[PluginRule, ...] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        self.rules = rules or (
            PluginRule("TEST001", Severity.WARNING, "Synthetic plugin test rule."),
        )
        self._findings = findings
        self._error = error

    def scan(self, repository: RepositoryView, config: object) -> tuple[PluginFinding, ...]:
        del repository, config
        if self._error is not None:
            raise self._error
        return self._findings


class _EvidenceScanner(_Scanner):
    def scan(self, repository: RepositoryView, config: object) -> tuple[PluginFinding, ...]:
        del config
        repository.files(["*.txt"])
        return self._findings


def _entry_point(
    plugin_id: str,
    scanner: object,
    *,
    distribution: str = "fixture-plugin",
    version: str = "1.2.3",
) -> PluginEntryPoint:
    return PluginEntryPoint(plugin_id, distribution, version, lambda: scanner)


def _loaded(
    plugin_id: str,
    scanner: object,
    *,
    config: dict[str, object] | None = None,
    overrides: dict[str, str] | None = None,
) -> LoadedPlugin:
    return load_plugins(
        [plugin_id],
        {
            "plugins": {
                plugin_id: {
                    "config": config or {},
                    "rules": overrides or {},
                }
            }
        },
        entry_points=[_entry_point(plugin_id, scanner)],
    )[0]


def test_installed_fixture_distribution_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(FIXTURE_SITE))
    distributions = tuple(metadata.distributions(path=[str(FIXTURE_SITE)]))
    entry_points = discover_plugin_entry_points(distributions)

    identities = [
        (item.name, item.distribution_name, item.distribution_version)
        for item in entry_points
    ]
    assert identities == [
        ("sample.crash", "repoinvariant-sample-plugin", "1.0.0"),
        ("sample.todo", "repoinvariant-sample-plugin", "1.0.0"),
    ]
    assert all(item.name != "ignored" for item in entry_points)

    target = tmp_path / "dir with space" / "checks.todo"
    target.parent.mkdir()
    target.write_text("OK\nTODO\n", encoding="utf-8")
    config = {
        "plugins": {
            "sample.todo": {
                "config": {"marker": "TODO", "patterns": ["**/*.todo"]},
                "rules": {"TODO001": "error"},
            }
        }
    }
    plugins = load_plugins(["sample.todo"], config, entry_points=entry_points)
    result = scan_plugins(tmp_path, plugins)

    assert [finding.code for finding in result.findings] == ["sample.todo:TODO001"]
    assert result.findings[0].severity is Severity.ERROR
    assert result.findings[0].location == Location(
        Path("dir with space/checks.todo"), 2, 1
    )
    assert result.scanned_files == {Path("dir with space/checks.todo")}
    scope = plugin_scope_payload(plugins)
    assert scope[0]["id"] == "sample.todo"
    assert scope[0]["distribution"] == {
        "name": "repoinvariant-sample-plugin",
        "version": "1.0.0",
    }
    assert scope[0]["config"] == {"marker": "TODO", "patterns": ["**/*.todo"]}
    assert scope[0]["rule_overrides"] == {"TODO001": "error"}


@pytest.mark.parametrize(
    ("shadow_source", "shadow_shape"),
    [
        ("repository", "module"),
        ("repository", "package"),
        ("pythonpath", "module"),
        ("pythonpath", "package"),
        ("repository-parent", "package"),
    ],
)
def test_installed_plugin_ignores_local_target_shadows_in_subprocess(
    tmp_path: Path, shadow_source: str, shadow_shape: str
) -> None:
    repository = (
        tmp_path / "repoinvariant_sample_plugin"
        if shadow_source == "repository-parent"
        else tmp_path / "repository"
    )
    repository.mkdir()
    shadow_root = tmp_path / "pythonpath" if shadow_source == "pythonpath" else repository
    shadow_root.mkdir(exist_ok=True)
    marker = tmp_path / f"{shadow_source}-{shadow_shape}-executed"
    sentinel = (
        f"open({str(marker)!r}, 'w').write('executed')\n"
        "raise RuntimeError('LOCAL_SHADOW_EXECUTED')\n"
    )
    if shadow_source == "repository-parent":
        (repository / "__init__.py").write_text(sentinel, encoding="utf-8")
    elif shadow_shape == "module":
        (shadow_root / "repoinvariant_sample_plugin.py").write_text(
            sentinel, encoding="utf-8"
        )
    else:
        package = shadow_root / "repoinvariant_sample_plugin"
        package.mkdir()
        (package / "__init__.py").write_text(sentinel, encoding="utf-8")

    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    if shadow_source == "pythonpath":
        environment["PYTHONPATH"] = str(shadow_root)
    parent_insertion = (
        f"sys.path.insert(0, {str(repository.parent)!r})\n"
        if shadow_source == "repository-parent"
        else ""
    )
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.append({str(FIXTURE_SITE)!r})\n"
        "from repoinvariant.plugin_api import load_plugins\n"
        f"{parent_insertion}"
        "plugins = load_plugins(['sample.todo'], {}, repository_root=Path.cwd())\n"
        "print(plugins[0].plugin_id)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "sample.todo\n"
    assert not marker.exists()


def test_preloaded_local_plugin_target_fails_before_loader_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = ModuleType("repoinvariant_sample_plugin")
    target.__file__ = str(tmp_path / "repoinvariant_sample_plugin.py")
    monkeypatch.setitem(sys.modules, target.__name__, target)
    calls: list[str] = []
    candidate = PluginEntryPoint(
        "sample.todo",
        "fixture-plugin",
        "1.0.0",
        lambda: calls.append("loaded"),
        target_module=target.__name__,
    )

    with pytest.raises(PluginError, match="could not be loaded"):
        load_plugins(
            ["sample.todo"],
            {},
            repository_root=tmp_path,
            entry_points=[candidate],
        )

    assert calls == []


def test_plugins_require_explicit_safe_unique_selection() -> None:
    calls: list[str] = []
    candidate = PluginEntryPoint(
        "sample.todo",
        "fixture-plugin",
        "1.0.0",
        lambda: calls.append("loaded"),
    )

    assert load_plugins([], {}, entry_points=[candidate]) == ()
    assert calls == []
    with pytest.raises(PluginError, match="invalid ID"):
        load_plugins(["module:object"], {}, entry_points=[candidate])
    with pytest.raises(PluginError, match="duplicate ID"):
        load_plugins(["sample.todo", "sample.todo"], {}, entry_points=[candidate])
    assert calls == []


def test_selected_plugins_load_in_deterministic_id_order() -> None:
    loaded: list[str] = []

    def candidate(plugin_id: str) -> PluginEntryPoint:
        def load() -> _Scanner:
            loaded.append(plugin_id)
            return _Scanner(plugin_id)

        return PluginEntryPoint(plugin_id, f"{plugin_id}-dist", "1.0.0", load)

    plugins = load_plugins(
        ["zeta.scan", "alpha.scan"],
        {},
        entry_points=[candidate("zeta.scan"), candidate("alpha.scan")],
    )

    assert [plugin.plugin_id for plugin in plugins] == ["alpha.scan", "zeta.scan"]
    assert loaded == ["alpha.scan", "zeta.scan"]


@pytest.mark.parametrize(
    ("entry_points", "message"),
    [
        ([], "not installed"),
        (
            [
                _entry_point("sample.todo", _Scanner("sample.todo")),
                _entry_point("sample.todo", _Scanner("sample.todo"), version="2.0.0"),
            ],
            "duplicate entry points",
        ),
        (
            [_entry_point("sample.todo", _Scanner("another.id"))],
            "incompatible",
        ),
    ],
)
def test_missing_duplicate_and_incompatible_plugins_fail_closed(
    entry_points: list[PluginEntryPoint], message: str
) -> None:
    with pytest.raises(PluginError, match=message):
        load_plugins(["sample.todo"], {}, entry_points=entry_points)


def test_incompatible_api_and_duplicate_rules_fail_closed() -> None:
    incompatible = _Scanner("sample.todo")
    incompatible.api_version = 2
    with pytest.raises(PluginError, match="API version"):
        load_plugins(
            ["sample.todo"],
            {},
            entry_points=[_entry_point("sample.todo", incompatible)],
        )

    duplicate = _Scanner(
        "sample.todo",
        rules=(
            PluginRule("TEST001", Severity.ERROR, "First."),
            PluginRule("TEST001", Severity.WARNING, "Second."),
        ),
    )
    with pytest.raises(PluginError, match="invalid rules"):
        load_plugins(
            ["sample.todo"],
            {},
            entry_points=[_entry_point("sample.todo", duplicate)],
        )


def test_unexpected_load_and_scan_exceptions_are_redacted(tmp_path: Path) -> None:
    def broken_loader() -> object:
        raise RuntimeError("PRIVATE_LOAD_VALUE")

    with pytest.raises(PluginError) as load_error:
        load_plugins(
            ["sample.todo"],
            {},
            entry_points=[
                PluginEntryPoint(
                    "sample.todo",
                    "fixture-plugin",
                    "1.0.0",
                    broken_loader,
                )
            ],
        )
    assert "PRIVATE_LOAD_VALUE" not in str(load_error.value)

    plugin = _loaded(
        "sample.todo",
        _Scanner("sample.todo", error=RuntimeError("PRIVATE_REPOSITORY_CONTENT")),
    )
    with pytest.raises(PluginError) as scan_error:
        scan_plugins(tmp_path, [plugin])
    assert "PRIVATE_REPOSITORY_CONTENT" not in str(scan_error.value)


def test_repository_view_accepts_only_bounded_repository_files(tmp_path: Path) -> None:
    (tmp_path / "ok.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "bad.txt").write_bytes(b"\xff")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    (tmp_path / "large.txt").write_bytes(b"x" * (MAX_SCAN_BYTES + 1))
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    view = RepositoryView(tmp_path)

    assert view.read_text("ok.txt") == "ok"
    for path in ("../outside.txt", str(outside), "link.txt", "pipe", "large.txt", "bad.txt"):
        with pytest.raises((OSError, UnicodeError, ValueError)):
            view.read_text(path)
    with pytest.raises(ValueError, match="symbolic links"):
        view.files(["*.txt"])

    (tmp_path / "link.txt").unlink()
    assert view.files(["*.txt"]) == ("bad.txt", "large.txt", "ok.txt")


def test_repository_view_rejects_overlong_paths_during_discovery(tmp_path: Path) -> None:
    relative = Path(*(["a" * 100] * 5), "check.todo")
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text("TODO\n", encoding="utf-8")
    assert len(relative.as_posix()) > 512

    view = RepositoryView(tmp_path)

    with pytest.raises(ValueError, match="up to 512 characters"):
        view.files(["**/*.todo"])


@pytest.mark.parametrize(
    "finding",
    [
        PluginFinding("UNKNOWN", "message", "key"),
        PluginFinding("TEST001", "", "key"),
        PluginFinding("TEST001", "x" * 2_049, "key"),
        PluginFinding("TEST001", "message", "bad key"),
        PluginFinding([], "message", "key"),
        PluginFinding("TEST001", "message", "key", PluginLocation("missing.txt")),
        PluginFinding(
            "TEST001",
            "message",
            "key",
            related=(PluginLocation("missing.txt"),) * 33,
        ),
    ],
)
def test_malformed_plugin_evidence_fails_closed(
    tmp_path: Path, finding: PluginFinding
) -> None:
    plugin = _loaded("sample.todo", _Scanner("sample.todo", findings=(finding,)))

    with pytest.raises(PluginError):
        scan_plugins(tmp_path, [plugin])


def test_finding_count_duplicate_identity_and_rule_overrides_are_enforced(
    tmp_path: Path,
) -> None:
    finding = PluginFinding("TEST001", "message", "stable-key")
    too_many = _loaded(
        "sample.todo",
        _Scanner("sample.todo", findings=(finding,) * (MAX_PLUGIN_FINDINGS + 1)),
    )
    with pytest.raises(PluginError, match="invalid evidence"):
        scan_plugins(tmp_path, [too_many])

    duplicate = _loaded(
        "sample.todo",
        _Scanner("sample.todo", findings=(finding, finding)),
    )
    with pytest.raises(PluginError, match="duplicate evidence"):
        scan_plugins(tmp_path, [duplicate])

    disabled = _loaded(
        "sample.todo",
        _Scanner("sample.todo", findings=(finding,)),
        overrides={"TEST001": "off"},
    )
    assert scan_plugins(tmp_path, [disabled]).findings == []

    disabled_duplicate = _loaded(
        "sample.todo",
        _Scanner("sample.todo", findings=(finding, finding)),
        overrides={"TEST001": "off"},
    )
    with pytest.raises(PluginError, match="duplicate evidence"):
        scan_plugins(tmp_path, [disabled_duplicate])


def test_disabled_findings_still_count_toward_aggregate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_api, "MAX_PLUGIN_FINDINGS", 3)
    plugins = [
        _loaded(
            plugin_id,
            _Scanner(
                plugin_id,
                findings=(
                    PluginFinding("TEST001", "message", f"{plugin_id}-one"),
                    PluginFinding("TEST001", "message", f"{plugin_id}-two"),
                ),
            ),
            overrides={"TEST001": "off"},
        )
        for plugin_id in ("sample.first", "sample.second")
    ]

    with pytest.raises(PluginError, match="exceed"):
        scan_plugins(tmp_path, plugins)


def test_plugin_baseline_key_accepts_documented_512_character_boundary(
    tmp_path: Path,
) -> None:
    raw_key = "k" * 512
    plugin = _loaded(
        "sample.todo",
        _Scanner(
            "sample.todo",
            findings=(PluginFinding("TEST001", "message", raw_key),),
        ),
    )

    result = scan_plugins(tmp_path, [plugin])

    assert result.findings[0].baseline_key == f"plugin:sample.todo:TEST001:{raw_key}"


def test_plugin_baseline_key_rejects_more_than_512_characters(tmp_path: Path) -> None:
    plugin = _loaded(
        "sample.todo",
        _Scanner(
            "sample.todo",
            findings=(PluginFinding("TEST001", "message", "k" * 513),),
        ),
    )

    with pytest.raises(PluginError, match="invalid evidence"):
        scan_plugins(tmp_path, [plugin])


def test_plugin_finding_order_is_independent_of_return_order(tmp_path: Path) -> None:
    for path in ("main.txt", "a.txt", "b.txt"):
        (tmp_path / path).write_text("evidence\n", encoding="utf-8")
    location = PluginLocation("main.txt", 1, 1)
    findings = (
        PluginFinding("TEST001", "column", "column-two", PluginLocation("main.txt", 1, 2)),
        PluginFinding("TEST001", "column", "column-one", location),
        PluginFinding("TEST001", "hint", "hint-z", location, hint="z"),
        PluginFinding("TEST001", "hint", "hint-a", location, hint="a"),
        PluginFinding(
            "TEST001",
            "related",
            "related-b",
            location,
            hint="same",
            related=(PluginLocation("b.txt"),),
        ),
        PluginFinding(
            "TEST001",
            "related",
            "related-a",
            location,
            hint="same",
            related=(PluginLocation("a.txt"),),
        ),
        PluginFinding("TEST001", "baseline", "baseline-b", location, hint="same"),
        PluginFinding("TEST001", "baseline", "baseline-a", location, hint="same"),
    )

    def scan(returned: tuple[PluginFinding, ...]):
        plugin = _loaded("sample.todo", _EvidenceScanner("sample.todo", findings=returned))
        return scan_plugins(tmp_path, [plugin]).findings

    def signatures(returned: tuple[PluginFinding, ...]) -> list[tuple[object, ...]]:
        return [
            (
                finding.location.column if finding.location else 0,
                finding.message,
                finding.hint,
                tuple(location.path.as_posix() for location in finding.related),
                finding.baseline_key,
            )
            for finding in scan(returned)
        ]

    assert signatures(findings) == signatures(tuple(reversed(findings)))


def test_unknown_plugin_rule_override_fails_before_scanning() -> None:
    with pytest.raises(PluginError, match="rule configuration"):
        _loaded(
            "sample.todo",
            _Scanner("sample.todo"),
            overrides={"UNKNOWN": "warning"},
        )


def test_public_entry_point_group_is_versioned() -> None:
    assert ENTRY_POINT_GROUP == "repoinvariant.scanners.v1"
