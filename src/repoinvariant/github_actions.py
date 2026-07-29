"""Safe GitHub Actions annotations, summaries, and step outputs."""

from __future__ import annotations

import os
import secrets
import sys
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from repoinvariant.models import Finding, ScanResult, Severity
from repoinvariant.reporters import render_markdown

_MAX_ANNOTATIONS = 50
_MAX_ANNOTATION_MESSAGE_CHARS = 8 * 1024
_MAX_ANNOTATION_PROPERTY_CHARS = 4 * 1024
_MAX_STEP_SUMMARY_BYTES = 256 * 1024
_SUMMARY_TRUNCATION_NOTICE = (
    "\n\n> RepoInvariant summary truncated. See the full report or workflow log for all findings.\n"
)

_ANNOTATION_COMMAND = {
    Severity.ERROR: "error",
    Severity.WARNING: "warning",
    Severity.NOTE: "notice",
}


def _visible_command_text(value: str) -> str:
    """Make non-line control characters inert without changing command newlines yet."""

    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character in {"\r", "\n"}:
            escaped.append(character)
        elif unicodedata.category(character).startswith("C") or character in {
            "\u2028",
            "\u2029",
        }:
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _bounded_command_text(value: str, limit: int) -> str:
    visible = _visible_command_text(value)
    if len(visible) <= limit:
        return visible
    return visible[: limit - 1] + "…"


def _escape_command_data(value: str) -> str:
    """Escape workflow-command data exactly as ``@actions/core`` does."""

    return (
        _bounded_command_text(value, _MAX_ANNOTATION_MESSAGE_CHARS)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _escape_command_property(value: str) -> str:
    """Escape workflow-command properties exactly as ``@actions/core`` does."""

    return (
        _bounded_command_text(value, _MAX_ANNOTATION_PROPERTY_CHARS)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _workspace_path(environ: Mapping[str, str]) -> Path | None:
    value = environ.get("GITHUB_WORKSPACE")
    return Path(value) if value else None


def _workspace_relative_path(path: Path, root: Path, workspace: Path | None) -> str | None:
    """Return a workspace-relative path only when it is also contained by the scan root."""

    if workspace is None:
        return None
    try:
        resolved_root = root.resolve()
        resolved_workspace = workspace.resolve()
        resolved_root.relative_to(resolved_workspace)
        candidate = path if path.is_absolute() else resolved_root / path
        resolved_candidate = candidate.resolve()
        resolved_candidate.relative_to(resolved_root)
        relative = resolved_candidate.relative_to(resolved_workspace)
    except (OSError, RuntimeError, ValueError):
        return None
    return relative.as_posix() if relative.parts else None


def _annotation(finding: Finding, root: Path, workspace: Path | None) -> str:
    command = _ANNOTATION_COMMAND[finding.severity]
    properties: list[str] = []
    if finding.location is not None:
        location = _workspace_relative_path(finding.location.path, root, workspace)
        if location is not None:
            properties.extend(
                (
                    f"file={_escape_command_property(location)}",
                    f"line={max(finding.location.line, 1)}",
                    f"col={max(finding.location.column, 1)}",
                )
            )
    properties.append(
        f"title={_escape_command_property(f'RepoInvariant {finding.code}')}"
    )

    message = finding.message
    if finding.hint:
        message = f"{message} Hint: {finding.hint}"
    return f"::{command} {','.join(properties)}::{_escape_command_data(message)}\n"


def _annotations(result: ScanResult, root: Path, workspace: Path | None) -> str:
    findings = result.sorted_findings()
    rendered = [
        _annotation(finding, root, workspace) for finding in findings[:_MAX_ANNOTATIONS]
    ]
    omitted = len(findings) - _MAX_ANNOTATIONS
    if omitted > 0:
        rendered.append(
            "::notice title=RepoInvariant::"
            f"{omitted} additional findings omitted; see the report or workflow log.\n"
        )
    return "".join(rendered)


def _bounded_summary(result: ScanResult, root: Path, fail_on: Severity) -> str:
    summary = render_markdown(result, root, fail_on)
    encoded = summary.encode("utf-8")
    if len(encoded) <= _MAX_STEP_SUMMARY_BYTES:
        return summary

    notice = _SUMMARY_TRUNCATION_NOTICE.encode("utf-8")
    prefix = encoded[: _MAX_STEP_SUMMARY_BYTES - len(notice)].decode(
        "utf-8", errors="ignore"
    )
    # End at a complete Markdown line so a partially emitted table row cannot alter the notice.
    if "\n" in prefix:
        prefix = prefix.rsplit("\n", 1)[0]
    bounded = prefix.rstrip() + _SUMMARY_TRUNCATION_NOTICE
    if len(bounded.encode("utf-8")) > _MAX_STEP_SUMMARY_BYTES:
        raise AssertionError("bounded GitHub summary exceeded its byte limit")
    return bounded


def _required_environment_file(environ: Mapping[str, str], name: str) -> Path:
    value = environ.get(name)
    if not value:
        raise ValueError(f"{name} is not set")
    return Path(value)


def _append_utf8(path: Path, content: str) -> None:
    """Append UTF-8 content without joining an existing unterminated record."""

    encoded = content.encode("utf-8")
    with path.open("a+b") as destination:
        destination.seek(0, os.SEEK_END)
        size = destination.tell()
        if size:
            destination.seek(-1, os.SEEK_END)
            last_byte = destination.read(1)
            destination.seek(0, os.SEEK_END)
            if last_byte not in {b"\r", b"\n"}:
                destination.write(b"\n")
        destination.write(encoded)


def _output_delimiter(value: str) -> str:
    lines = set(value.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    for _ in range(128):
        candidate = f"repoinvariant_{secrets.token_hex(16)}"
        if candidate not in lines:
            return candidate
    raise ValueError("could not create a safe GitHub output delimiter")


def _output_record(name: str, value: str) -> str:
    delimiter = _output_delimiter(value)
    return f"{name}<<{delimiter}\n{value}\n{delimiter}\n"


def _safe_report_path_value(value: str | None) -> str:
    if value is None:
        return ""
    if any(
        unicodedata.category(character).startswith("C")
        or character in {"\u2028", "\u2029"}
        for character in value
    ):
        return ""
    return value


def _report_path_value(
    report_path: Path | None, root: Path, workspace: Path | None
) -> str:
    if report_path is None:
        return ""
    if workspace is not None:
        return _safe_report_path_value(_workspace_relative_path(report_path, root, workspace))
    try:
        resolved_root = root.resolve()
        candidate = report_path if report_path.is_absolute() else resolved_root / report_path
        relative = candidate.resolve().relative_to(resolved_root).as_posix()
        return _safe_report_path_value(relative)
    except (OSError, RuntimeError, ValueError):
        return ""


def _outputs(
    result: ScanResult,
    root: Path,
    fail_on: Severity,
    report_path: Path | None,
    workspace: Path | None,
) -> str:
    values = (
        ("errors", str(result.error_count)),
        ("warnings", str(result.warning_count)),
        ("status", "fail" if result.blocks(fail_on) else "pass"),
        ("report-path", _report_path_value(report_path, root, workspace)),
    )
    return "".join(_output_record(name, value) for name, value in values)


def emit_github_feedback(
    result: ScanResult,
    root: Path,
    fail_on: Severity,
    report_path: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
    stream: TextIO | None = None,
) -> None:
    """Emit bounded annotations and append a summary and composite-action outputs.

    ``report_path`` is the actual report destination (normally the value returned by
    ``atomic_write_text``). It is exposed only as a portable, workspace-relative path.
    Environment-file and stream errors deliberately propagate so the CLI can fail closed.
    """

    environment = os.environ if environ is None else environ
    output_stream = sys.stdout if stream is None else stream
    workspace = _workspace_path(environment)
    output_path = _required_environment_file(environment, "GITHUB_OUTPUT")
    summary_path = _required_environment_file(environment, "GITHUB_STEP_SUMMARY")

    annotations = _annotations(result, root, workspace)
    summary = _bounded_summary(result, root, fail_on)
    outputs = _outputs(result, root, fail_on, report_path, workspace)

    _append_utf8(output_path, outputs)
    _append_utf8(summary_path, summary)
    output_stream.write(annotations)
    output_stream.flush()
