import io
from pathlib import Path

import pytest

import repoinvariant.github_actions as github_actions
from repoinvariant.github_actions import emit_github_feedback
from repoinvariant.models import Finding, Location, ScanResult, Severity


def _environment(tmp_path: Path, workspace: Path) -> tuple[dict[str, str], Path, Path]:
    output = tmp_path / "github-output.txt"
    summary = tmp_path / "github-summary.md"
    return (
        {
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_WORKSPACE": str(workspace),
        },
        output,
        summary,
    )


def _parse_outputs(text: str) -> dict[str, str]:
    lines = text.splitlines()
    outputs: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if "<<" in line:
            name, delimiter = line.split("<<", 1)
            value_lines: list[str] = []
            while index < len(lines) and lines[index] != delimiter:
                value_lines.append(lines[index])
                index += 1
            assert index < len(lines), f"unterminated output record for {name}"
            index += 1
            outputs[name] = "\n".join(value_lines)
        elif "=" in line:
            name, value = line.split("=", 1)
            outputs[name] = value
    return outputs


def test_annotations_escape_commands_and_use_nested_workspace_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "services" / "api"
    root.mkdir(parents=True)
    environment, _, _ = _environment(tmp_path, workspace)
    stream = io.StringIO()
    result = ScanResult(
        findings=[
            Finding(
                code="ENV,:\n001",
                message="unsafe%\r\n::warning injected",
                severity=Severity.ERROR,
                location=Location(Path("bad%,:\n::error.yml"), 0, -3),
                hint="use a safe value\x1b[31m",
            )
        ]
    )

    emit_github_feedback(
        result,
        root,
        Severity.ERROR,
        None,
        environ=environment,
        stream=stream,
    )

    annotation = stream.getvalue()
    assert annotation.count("\n") == 1
    assert "\r" not in annotation
    assert "\x1b" not in annotation
    assert "file=services/api/bad%25%2C%3A%0A%3A%3Aerror.yml" in annotation
    assert ",line=1,col=1," in annotation
    assert "title=RepoInvariant ENV%2C%3A%0A001" in annotation
    assert "unsafe%25%0D%0A::warning injected" in annotation
    assert "Hint: use a safe value\\x1b[31m" in annotation


def test_location_outside_workspace_becomes_a_global_annotation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "outside"
    root.mkdir()
    environment, _, _ = _environment(tmp_path, workspace)
    stream = io.StringIO()
    result = ScanResult(
        findings=[
            Finding(
                "ENV001",
                "outside",
                Severity.ERROR,
                Location(Path("compose.yml"), 7, 5),
            )
        ]
    )

    emit_github_feedback(
        result,
        root,
        Severity.ERROR,
        None,
        environ=environment,
        stream=stream,
    )

    annotation = stream.getvalue()
    assert annotation == "::error title=RepoInvariant ENV001::outside\n"
    assert str(root) not in annotation


def test_annotations_are_capped_and_report_the_omitted_count(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment, _, _ = _environment(tmp_path, workspace)
    stream = io.StringIO()
    result = ScanResult(
        findings=[
            Finding(f"ENV{index:03}", f"finding {index}", Severity.ERROR)
            for index in range(52)
        ]
    )

    emit_github_feedback(
        result,
        workspace,
        Severity.ERROR,
        None,
        environ=environment,
        stream=stream,
    )

    annotations = stream.getvalue()
    assert annotations.count("::error ") == 50
    assert annotations.count("::notice ") == 1
    assert "2 additional findings omitted" in annotations


def test_outputs_are_appended_and_newline_in_report_path_cannot_inject(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    root = workspace / "service"
    root.mkdir(parents=True)
    environment, output_path, summary_path = _environment(tmp_path, workspace)
    output_path.write_text("sentinel=keep", encoding="utf-8")
    summary_path.write_text("Before", encoding="utf-8")
    report_path = root / "reports" / "check\nstatus=hacked.md"
    result = ScanResult(
        findings=[Finding("ENV002", "unused", Severity.WARNING)],
        scanned_files={Path(".env.example")},
    )

    emit_github_feedback(
        result,
        root,
        Severity.ERROR,
        report_path,
        environ=environment,
        stream=io.StringIO(),
    )

    first_outputs = _parse_outputs(output_path.read_text(encoding="utf-8"))
    assert first_outputs == {
        "sentinel": "keep",
        "errors": "0",
        "warnings": "1",
        "status": "pass",
        "report-path": "",
    }
    assert summary_path.read_text(encoding="utf-8").startswith(
        "Before\n# RepoInvariant report\n"
    )

    emit_github_feedback(
        result,
        root,
        Severity.WARNING,
        report_path,
        environ=environment,
        stream=io.StringIO(),
    )
    second_outputs = _parse_outputs(output_path.read_text(encoding="utf-8"))
    assert second_outputs["status"] == "fail"
    assert second_outputs["errors"] == "0"
    assert second_outputs["warnings"] == "1"


def test_output_heredoc_retries_a_colliding_random_delimiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = iter(("collision", "safe"))
    monkeypatch.setattr(github_actions.secrets, "token_hex", lambda _: next(tokens))
    value = "first\nrepoinvariant_collision\nstatus=hacked"

    record = github_actions._output_record("report-path", value)

    assert "report-path<<repoinvariant_safe\n" in record
    assert _parse_outputs(record) == {"report-path": value}


def test_step_summary_is_bounded_and_appended(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment, _, summary_path = _environment(tmp_path, workspace)
    prefix = b"existing summary\n"
    summary_path.write_bytes(prefix)
    result = ScanResult(
        findings=[
            Finding(f"ENV{index:03}", "x" * 3000, Severity.ERROR)
            for index in range(150)
        ]
    )

    emit_github_feedback(
        result,
        workspace,
        Severity.ERROR,
        None,
        environ=environment,
        stream=io.StringIO(),
    )

    summary = summary_path.read_bytes()
    assert summary.startswith(prefix)
    assert len(summary) - len(prefix) <= 256 * 1024
    assert b"RepoInvariant summary truncated" in summary


def test_environment_file_write_errors_propagate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment, _, _ = _environment(tmp_path, workspace)
    environment["GITHUB_OUTPUT"] = str(tmp_path)

    with pytest.raises(IsADirectoryError):
        emit_github_feedback(
            ScanResult(),
            workspace,
            Severity.ERROR,
            None,
            environ=environment,
            stream=io.StringIO(),
        )


def test_default_annotation_stream_uses_stdout_for_runner_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment, _, _ = _environment(tmp_path, workspace)
    result = ScanResult(findings=[Finding("ENV001", "drift", Severity.ERROR)])

    emit_github_feedback(result, workspace, Severity.ERROR, None, environ=environment)

    captured = capsys.readouterr()
    assert captured.out == "::error title=RepoInvariant ENV001::drift\n"
    assert captured.err == ""
