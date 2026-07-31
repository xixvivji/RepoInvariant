# Releasing RepoInvariant

## One-time PyPI setup

RepoInvariant publishes without a long-lived API token. Create a pending GitHub Trusted Publisher at
<https://pypi.org/manage/account/publishing/> with these exact values:

| Field | Value |
|---|---|
| PyPI project name | `repoinvariant` |
| Owner | `xixvivji` |
| Repository | `RepoInvariant` |
| Workflow | `release.yml` |
| Environment | `pypi` |

Create a GitHub environment named `pypi` and require manual approval when the account supports an
eligible reviewer. The release workflow grants `id-token: write` only to the publish job.

## Release checklist

1. Start `release/X.Y.Z` from `develop`.
2. Set `__version__` in `src/repoinvariant/__init__.py` to `X.Y.Z` (package metadata reads it).
3. Move the changelog entries from Unreleased to `[X.Y.Z] - YYYY-MM-DD`.
4. Run:

   ```bash
   uv sync --frozen --extra dev
   uv run ruff check .
   uv run pytest --cov=repoinvariant --cov-fail-under=85
   uv run repoinvariant check . --fail-on warning
   uv build --no-build-isolation
   uv run twine check --strict dist/*
   ```

5. Open the release pull request into `main` and wait for CI.
6. Merge it, then create and push an annotated tag:

   ```bash
   git switch main
   git pull --ff-only
   git tag -a vX.Y.Z -m "RepoInvariant vX.Y.Z"
   git push origin vX.Y.Z
   ```

   If a local tag push is unavailable, open **Actions → Release → Run workflow**, select `main`,
   enter `vX.Y.Z`, and enable `publish`. The manual path runs the complete validation gate before
   creating an annotated tag at the selected `main` commit. Leaving `publish` disabled remains a
   validation-only dry run. The tag created with `GITHUB_TOKEN` deliberately does not start a
   second workflow; the dispatch that created it continues through provenance and publishing.
   Never dispatch publishing from another branch or reuse a published tag.

7. Approve the protected `pypi` environment deployment.
8. Verify the PyPI files, attestations, and GitHub release assets.
9. Merge `main` back into `develop` and delete `release/X.Y.Z`.

The workflow refuses a tag whose value differs from the package `__version__`. PyPI Trusted
Publishing generates short-lived credentials and distribution attestations; no PyPI token belongs
in repository secrets. Protect `v*` with a tag ruleset that blocks updates and deletions. If a
post-PyPI GitHub Release step fails, rerun only the failed jobs; the release job can resume a
matching draft and refuses to overwrite a published release.
