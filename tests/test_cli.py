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
