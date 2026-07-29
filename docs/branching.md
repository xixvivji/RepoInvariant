# Git Flow

RepoInvariant uses a small, reviewable Git Flow model.

| Branch | Purpose | Merge target |
|---|---|---|
| `main` | Released source only; every commit is release-ready | — |
| `develop` | Integration branch for the next release | `main` through `release/*` |
| `feature/*` | One feature, fix, or hardening unit | `develop` |
| `release/*` | Version, changelog, and release-only stabilization | `main`, then back to `develop` |
| `hotfix/*` | Urgent correction based on `main` | `main`, then back to `develop` |

## Feature work

```bash
git switch develop
git pull --ff-only
git switch -c feature/short-description
# implement and validate
git push -u origin feature/short-description
```

Open a pull request into `develop`. Keep the branch focused and delete it after merge.

## Release work

Create `release/X.Y.Z` from `develop`, update the version and changelog, and run every check.
Open a pull request into `main`. After it merges:

1. create the annotated `vX.Y.Z` tag on the merge commit;
2. push the tag, which starts the release workflow;
3. merge `main` back into `develop` without rewriting history;
4. delete the release branch after the back-merge is complete.

## Hotfix work

Create `hotfix/X.Y.Z` from `main`, validate it, and merge it to `main`. Tag the resulting release,
then merge `main` back into `develop` so the correction is not lost.

Never commit directly to `main`. Avoid force pushes and rebasing shared `develop`, `release/*`, or
`hotfix/*` branches.
