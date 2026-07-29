# RepoInvariant contributor guidance

## Product boundaries

- Keep scans deterministic, offline, and reproducible. The scanner must not require an API key,
  call an external service, or use semantic/AI matching.
- Findings may expose variable names, public `REQ-*` identifiers, and source locations, but never
  discovered secret values or arbitrary custom-pattern matches.
- Keep configured input files and report destinations inside the repository. Preserve the existing
  file-size, file-count, symlink, regular-file, and regex-time bounds.
- A parser should tolerate valid syntax it does not understand. Configured malformed YAML should
  fail closed with a concise command error instead of a traceback.

## Git workflow

- Use the Git Flow policy in `docs/branching.md`.
- Start `feature/*` branches from `develop` and merge them back into `develop` through a pull
  request.
- Release only through `release/* -> main`, tag the merge commit, then merge `main` back into
  `develop`.
- Do not rewrite shared branches or move published release tags.

## Implementation guidance

- Add the smallest synthetic fixture that demonstrates a contract mismatch.
- Keep finding order, report schemas, and CLI exit codes deterministic.
- When adding or changing a finding code, update configuration defaults, reporters, tests, the
  README finding table, and the changelog together.
- Treat GitHub workflow commands and environment files as untrusted output channels: escape command
  data and properties, and prevent newline injection in outputs.
- Keep GitHub Actions dependencies pinned to full commit SHAs.

## Verification

Run the full contributor gate after every code or workflow change:

```bash
uv sync --frozen --extra dev
uv run --frozen ruff check .
uv run --frozen pytest --cov=repoinvariant --cov-report=term-missing --cov-fail-under=85
uv run --frozen repoinvariant check . --fail-on warning
uv lock --check
uv build --no-build-isolation
uv run --frozen twine check --strict dist/*
```

Also run `actionlint` when `action.yml` or `.github/workflows/*` changes. Use only synthetic values
in tests, examples, issues, logs, and reports.
