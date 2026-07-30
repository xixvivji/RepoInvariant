"""Command-line interface for local use and CI merge gates."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from repoinvariant import __version__
from repoinvariant.baseline import (
    BaselineError,
    apply_baseline,
    create_baseline,
    load_baseline,
    render_baseline,
)
from repoinvariant.config import CONFIG_NAME, DEFAULT_CONFIG_TEXT, ConfigError, load_config
from repoinvariant.env_contracts import scan_env_contracts
from repoinvariant.filesystem import atomic_write_text
from repoinvariant.github_actions import emit_github_feedback
from repoinvariant.models import ScanResult, Severity
from repoinvariant.reporters import render, safe_console_text
from repoinvariant.traceability import scan_traceability
from repoinvariant.version_contracts import scan_version_contracts

BASELINE_NAME = ".repoinvariant-baseline.json"


def _print_error(error: object) -> None:
    print(f"repoinvariant: {safe_console_text(str(error))}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repoinvariant",
        description="Catch contract drift across repository artifacts before merge.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser("check", help="scan a repository and fail on contract drift")
    check.add_argument("path", nargs="?", default=".", type=Path, help="repository root")
    check.add_argument("--config", type=Path, help=f"config path (default: {CONFIG_NAME})")
    check.add_argument(
        "--format",
        choices=("text", "json", "markdown", "sarif"),
        default="text",
        dest="format_name",
    )
    check.add_argument("--output", type=Path, help="write the report to a file")
    check.add_argument(
        "--fail-on",
        choices=("error", "warning"),
        default="error",
        help="minimum finding severity that returns exit code 1",
    )
    check.add_argument("--no-env", action="store_true", help="skip environment contracts")
    check.add_argument("--no-features", action="store_true", help="skip feature traceability")
    check.add_argument("--no-versions", action="store_true", help="skip version contracts")
    check.add_argument(
        "--baseline",
        type=Path,
        help="suppress findings accepted in a repository baseline",
    )
    check.add_argument("--github-actions", action="store_true", help=argparse.SUPPRESS)

    baseline = subcommands.add_parser(
        "baseline",
        help="snapshot current findings for gradual adoption",
    )
    baseline.add_argument("path", nargs="?", default=".", type=Path, help="repository root")
    baseline.add_argument("--config", type=Path, help=f"config path (default: {CONFIG_NAME})")
    baseline.add_argument(
        "--output",
        type=Path,
        default=Path(BASELINE_NAME),
        help=f"baseline output path (default: {BASELINE_NAME})",
    )
    baseline.add_argument("--force", action="store_true", help="replace an existing baseline")
    baseline.add_argument("--no-env", action="store_true", help="skip environment contracts")
    baseline.add_argument("--no-features", action="store_true", help="skip feature traceability")
    baseline.add_argument("--no-versions", action="store_true", help="skip version contracts")

    init = subcommands.add_parser("init", help=f"create a starter {CONFIG_NAME}")
    init.add_argument("path", nargs="?", default=".", type=Path, help="repository root")
    init.add_argument("--force", action="store_true", help="replace an existing config")
    return parser


def _resolve_root(path: Path, *, create: bool = False) -> Path:
    try:
        root = path.expanduser().resolve()
        if create:
            root.mkdir(parents=True, exist_ok=True)
        elif not root.is_dir():
            raise ValueError(f"repository root does not exist: {root}")
    except (OSError, RuntimeError) as exc:
        action = "create" if create else "resolve"
        raise ValueError(f"cannot {action} repository root: {exc}") from exc
    return root


def _candidate_path(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    return Path(os.path.abspath(candidate))


def _same_path(root: Path, left: Path, right: Path) -> bool:
    left_candidate = _candidate_path(root, left)
    right_candidate = _candidate_path(root, right)
    if left_candidate == right_candidate or str(left_candidate).casefold() == str(
        right_candidate
    ).casefold():
        return True
    try:
        return os.path.samefile(left_candidate, right_candidate)
    except OSError:
        return False


def _scan(
    root: Path,
    config: dict[str, object],
    *,
    no_env: bool,
    no_features: bool,
    no_versions: bool,
) -> ScanResult:
    result = ScanResult()
    if not no_env:
        result.extend(scan_env_contracts(root, config))
    if not no_features:
        result.extend(scan_traceability(root, config))
    if not no_versions:
        result.extend(scan_version_contracts(root, config))
    return result


def _check(args: argparse.Namespace) -> int:
    baseline_counts: tuple[int, int] | None = None
    try:
        root = _resolve_root(args.path)
        if args.baseline and args.output and _same_path(root, args.baseline, args.output):
            raise ValueError("baseline file and report output must be different paths")
        config_path = args.config or Path(CONFIG_NAME)
        if args.baseline and _same_path(root, args.baseline, config_path):
            raise ValueError("baseline file and configuration file must be different paths")
        config = load_config(root, args.config)
        result = _scan(
            root,
            config,
            no_env=args.no_env,
            no_features=args.no_features,
            no_versions=args.no_versions,
        )
        if args.baseline:
            application = apply_baseline(
                result,
                load_baseline(root, args.baseline),
                config,
                no_env=args.no_env,
                no_features=args.no_features,
                no_versions=args.no_versions,
            )
            result = application.result
            baseline_counts = (application.suppressed_count, application.stale_count)
            if not args.github_actions:
                print(
                    "RepoInvariant baseline: "
                    f"{application.suppressed_count} suppressed, "
                    f"{application.stale_count} stale.",
                    file=sys.stderr,
                )
                if application.stale_count:
                    print(
                        "Review resolved findings, then regenerate the baseline "
                        "to remove stale entries.",
                        file=sys.stderr,
                    )
    except (BaselineError, ConfigError, OSError, UnicodeError, ValueError) as exc:
        _print_error(exc)
        return 2

    fail_on = Severity(args.fail_on)
    report = render(result, root, args.format_name, fail_on)
    output: Path | None = None
    try:
        if args.output:
            output = atomic_write_text(root, args.output, report, label="report output")
            print(
                f"RepoInvariant report written to {safe_console_text(str(output))}",
                file=sys.stderr,
            )
        else:
            destination = sys.stderr if args.github_actions else sys.stdout
            print(report, end="", file=destination)
        if args.github_actions:
            if baseline_counts is not None:
                suppressed_count, stale_count = baseline_counts
                print(
                    "::notice title=RepoInvariant baseline::"
                    f"{suppressed_count} suppressed, {stale_count} stale."
                )
            emit_github_feedback(result, root, fail_on, output)
    except (OSError, UnicodeError, ValueError) as exc:
        _print_error(exc)
        return 2

    return 1 if result.blocks(fail_on) else 0


def _baseline(args: argparse.Namespace) -> int:
    try:
        root = _resolve_root(args.path)
        config_path = args.config or Path(CONFIG_NAME)
        if _same_path(root, args.output, config_path):
            raise ValueError("baseline output and configuration file must be different paths")
        destination = _candidate_path(root, args.output)
        if (destination.exists() or destination.is_symlink()) and not args.force:
            raise ValueError(
                f"{destination} already exists (use --force to replace it after review)"
            )
        config = load_config(root, args.config)
        result = _scan(
            root,
            config,
            no_env=args.no_env,
            no_features=args.no_features,
            no_versions=args.no_versions,
        )
        baseline = create_baseline(
            result,
            config,
            no_env=args.no_env,
            no_features=args.no_features,
            no_versions=args.no_versions,
        )
        output = atomic_write_text(
            root,
            args.output,
            render_baseline(baseline),
            label="baseline output",
            overwrite=args.force,
        )
    except (BaselineError, ConfigError, OSError, UnicodeError, ValueError) as exc:
        _print_error(exc)
        return 2
    print(
        f"Created {safe_console_text(str(output))} with "
        f"{len(baseline.findings)} accepted finding(s)."
    )
    print(
        "Warning: this baseline suppresses matching findings. Generate or update it only "
        "from a trusted base branch, and review every baseline change.",
        file=sys.stderr,
    )
    return 0


def _init(args: argparse.Namespace) -> int:
    try:
        root = _resolve_root(args.path, create=True)
    except ValueError as exc:
        _print_error(exc)
        return 2
    destination = root / CONFIG_NAME
    if (destination.exists() or destination.is_symlink()) and not args.force:
        print(
            "repoinvariant: "
            f"{safe_console_text(str(destination))} already exists "
            "(use --force to replace it)",
            file=sys.stderr,
        )
        return 2
    try:
        atomic_write_text(root, destination, DEFAULT_CONFIG_TEXT, label="configuration file")
    except (OSError, UnicodeError, ValueError) as exc:
        _print_error(exc)
        return 2
    print(f"Created {safe_console_text(str(destination))}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _check(args)
    if args.command == "baseline":
        return _baseline(args)
    if args.command == "init":
        return _init(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
