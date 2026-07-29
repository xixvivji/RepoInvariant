import json
from pathlib import Path

from repotruth.cli import main


def test_init_creates_config_and_refuses_to_overwrite(tmp_path: Path, capsys) -> None:
    assert main(["init", str(tmp_path)]) == 0
    config = tmp_path / ".repotruth.yml"
    assert config.exists()

    assert main(["init", str(tmp_path)]) == 2
    assert "already exists" in capsys.readouterr().err


def test_check_clean_example_as_json(capsys) -> None:
    example = Path(__file__).parents[1] / "examples" / "ticket-service"

    assert main(["check", str(example), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"]["errors"] == 0
    assert payload["summary"]["files"] >= 5


def test_check_returns_one_for_drift(tmp_path: Path, capsys) -> None:
    (tmp_path / ".env.example").write_text(
        "DATABASE_URL=postgres://localhost/db\n", encoding="utf-8"
    )
    (tmp_path / "compose.yml").write_text(
        "services:\n  api:\n    environment:\n      REDIS_URL: ${REDIS_URL}\n",
        encoding="utf-8",
    )
    (tmp_path / ".repotruth.yml").write_text(
        """version: 1
env:
  contracts: [.env.example]
  compose: [compose.yml]
  kubernetes: []
  workflows: []
  spring: []
  ignore: []
features:
  requirements: []
  specifications: []
  tests: []
  id_pattern: '\\bREQ-[A-Z0-9][A-Z0-9-]*\\b'
  openapi_extension: x-feature-id
  ignore: []
""",
        encoding="utf-8",
    )

    assert main(["check", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "ENV001" in output
    assert "REDIS_URL" in output
    assert "postgres://" not in output


def test_check_accepts_option_like_repository_path(tmp_path: Path, capsys) -> None:
    root = tmp_path / "--help"
    root.mkdir()

    assert main(["check", "--format", "json", "--", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_check_fails_for_explicit_missing_config(tmp_path: Path, capsys) -> None:
    assert main(["check", str(tmp_path), "--config", "missing.yml"]) == 2
    assert "configuration file does not exist" in capsys.readouterr().err


def test_warning_failure_policy_matches_json_report(tmp_path: Path, capsys) -> None:
    (tmp_path / ".env.example").write_text("UNUSED=\n", encoding="utf-8")

    arguments = [
        "check",
        str(tmp_path),
        "--no-features",
        "--fail-on",
        "warning",
        "--format",
        "json",
    ]
    assert main(arguments) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 1
    assert payload["blocking_threshold"] == "warning"


def test_output_and_force_init_refuse_symlinks(tmp_path: Path, capsys) -> None:
    outside_config = tmp_path / "outside.yml"
    outside_config.write_text("do not replace\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".repotruth.yml").symlink_to(outside_config)

    assert main(["init", str(root), "--force"]) == 2
    assert outside_config.read_text(encoding="utf-8") == "do not replace\n"
    assert "symbolic link" in capsys.readouterr().err

    (root / ".repotruth.yml").unlink()
    output_target = tmp_path / "outside.json"
    output_target.write_text("do not replace\n", encoding="utf-8")
    (root / "report.json").symlink_to(output_target)

    assert main(["check", str(root), "--format", "json", "--output", "report.json"]) == 2
    assert output_target.read_text(encoding="utf-8") == "do not replace\n"
    assert "symbolic link" in capsys.readouterr().err
