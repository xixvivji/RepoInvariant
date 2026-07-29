"""Command-line interface for local use and CI merge gates."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from repotruth import __version__
from repotruth.config import CONFIG_NAME, DEFAULT_CONFIG_TEXT, ConfigError, load_config
from repotruth.env_contracts import scan_env_contracts
from repotruth.models import ScanResult
from repotruth.reporters import render
from repotruth.traceability import scan_traceability


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repotruth",
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

    init = subcommands.add_parser("init", help=f"create a starter {CONFIG_NAME}")
    init.add_argument("path", nargs="?", default=".", type=Path, help="repository root")
    init.add_argument("--force", action="store_true", help="replace an existing config")
    return parser


def _check(args: argparse.Namespace) -> int:
    root = args.path.expanduser().resolve()
    if not root.is_dir():
        print(f"repotruth: repository root does not exist: {root}", file=sys.stderr)
        return 2
    try:
        config = load_config(root, args.config)
        result = ScanResult()
        if not args.no_env:
            result.extend(scan_env_contracts(root, config))
        if not args.no_features:
            result.extend(scan_traceability(root, config))
    except (ConfigError, OSError, UnicodeError, ValueError) as exc:
        print(f"repotruth: {exc}", file=sys.stderr)
        return 2

    report = render(result, root, args.format_name)
    if args.output:
        output = args.output if args.output.is_absolute() else Path.cwd() / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(f"RepoTruth report written to {output}", file=sys.stderr)
    else:
        print(report, end="")

    if result.error_count:
        return 1
    if args.fail_on == "warning" and result.warning_count:
        return 1
    return 0


def _init(args: argparse.Namespace) -> int:
    root = args.path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / CONFIG_NAME
    if destination.exists() and not args.force:
        print(
            f"repotruth: {destination} already exists (use --force to replace it)",
            file=sys.stderr,
        )
        return 2
    destination.write_text(DEFAULT_CONFIG_TEXT, encoding="utf-8")
    print(f"Created {destination}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _check(args)
    if args.command == "init":
        return _init(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
