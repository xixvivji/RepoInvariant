# Contributing to RepoTruth

Thanks for helping make cross-artifact contracts boring and reliable.

## Development setup

RepoTruth requires Python 3.11 or newer and uses `uv` for the contributor workflow.

```bash
git clone https://github.com/xixvivji/RepoTruth.git
cd RepoTruth
uv sync --frozen --extra dev
uv run pytest
uv run ruff check .
uv build --no-build-isolation
```

## Branching

The project uses [Git Flow](docs/branching.md). Create `feature/*` branches from `develop` and open
feature pull requests back into `develop`. Only reviewed `release/*` and `hotfix/*` work reaches
`main`; do not commit directly to either long-lived branch.

## Before opening a pull request

1. Add a minimal fixture that reproduces the drift.
2. Add or update a deterministic test.
3. Keep secret values out of fixtures and snapshots.
4. Run tests, lint, build, and `uv run repotruth check .`.
5. Explain which two or more artifacts disagree and what the expected contract is.

Parser changes should tolerate valid syntax they do not understand. A crash is worse than a
clearly scoped unsupported case. Please avoid semantic or AI-based matching in the deterministic
core without first discussing the threat model and reproducibility trade-offs in an issue.

By submitting a contribution, you agree that it is licensed under Apache-2.0.
