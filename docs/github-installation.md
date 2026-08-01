# Complete GitHub installation

This checklist turns RepoInvariant into a merge gate and publishes its SARIF report to GitHub Code
Scanning. Start with a pull request so GitHub records the check name before you configure a ruleset.

## 1. Generate and review the configuration

From a trusted checkout of the default branch, install RepoInvariant and detect only artifact ranges
that already exist:

```console
uvx repoinvariant init --detect
uvx repoinvariant doctor --strict --verbose
```

`init --detect` emits deterministic built-in patterns, not arbitrary discovered filenames. It turns
off a rule family when no supported artifacts for that scanner were found. Review
`.repoinvariant.yml`, narrow broad patterns where needed, and opt in to required Java source ranges
only after the repository has one canonical Java major.

`doctor --strict` exits nonzero when an effective scanner scans no files or a source named in
`versions.java.required` is empty, unmatched, or entirely ignored. Scanner families whose rules are
all `off`, scanners disabled with `--no-*`, and optional unconfigured scanners do not fail strict
diagnosis.

## 2. Add the workflow

Copy [`examples/github/repoinvariant.yml`](../examples/github/repoinvariant.yml) to
`.github/workflows/repoinvariant.yml`. The complete copyable fixture is kept below as well.

<!-- consumer-workflow:start -->
```yaml
name: RepoInvariant

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  security-events: write

concurrency:
  group: repoinvariant-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  contracts:
    name: RepoInvariant contracts
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - name: Check out repository
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - name: Check repository contracts
        id: repoinvariant
        uses: xixvivji/RepoInvariant@ea489aa32cba6eb31760070eecde543726a91caf # v0.4.0
        with:
          path: .
          format: sarif
          output: repoinvariant-report.sarif
          fail-on: error
          strict: "true"
      - name: Preserve RepoInvariant SARIF artifact
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        if: always()
        with:
          name: repoinvariant-sarif
          path: ${{ steps.repoinvariant.outputs.report-path || 'repoinvariant-report.sarif' }}
          if-no-files-found: warn
          retention-days: 7
      - name: Upload RepoInvariant results to Code Scanning
        uses: github/codeql-action/upload-sarif@f205ea1c3313d32999d8d6a48b4f6530d4437b38 # v4.37.4
        if: >-
          always() &&
          steps.repoinvariant.outputs.report-path != '' &&
          (github.event_name != 'pull_request' ||
          github.event.pull_request.head.repo.full_name == github.repository)
        with:
          sarif_file: ${{ steps.repoinvariant.outputs.report-path }}
          category: repoinvariant
```
<!-- consumer-workflow:end -->

`strict: "true"` runs `doctor --strict` before `check` on every workflow invocation. The diagnosis
receives the same `path`, `config`, `baseline`, and `no-*` scanner selections as the contract check,
so deleting, renaming, or ignoring every target of an effective scanner cannot turn the required
check falsely green. An empty effective scanner or an unsatisfied required Java source returns exit
code `1`; invalid or unsafe configuration, baseline, or scanner input returns exit code `2`. In
either case the contract check does not run and the workflow job fails.

The fixture pins every third-party action to a full commit SHA. The CodeQL Action pin is the
official immutable [`v4.37.4` release](https://github.com/github/codeql-action/releases/tag/v4.37.4),
commit
[`f205ea1c3313d32999d8d6a48b4f6530d4437b38`](https://github.com/github/codeql-action/commit/f205ea1c3313d32999d8d6a48b4f6530d4437b38),
verified on 2026-08-01. Dependabot can keep GitHub Actions pins current without replacing immutable
SHAs with floating tags.

## 3. Verify the first pull request

Before making the check required, confirm all of the following on a test pull request:

- The `RepoInvariant contracts` check runs and fails when `fail-on` is reached.
- Deleting or renaming every target of one effective scanner fails during strict diagnosis before
  the contract check runs.
- The Actions summary contains RepoInvariant counts and annotations point to repository files.
- The `repoinvariant-sarif` workflow artifact exists even when drift fails the check.
- For a same-repository branch, **Security > Code scanning** shows the `repoinvariant` category.
- A fork pull request runs the contract check but skips the Code Scanning upload.

GitHub requires `security-events: write` for SARIF upload. Code scanning is available for public
repositories and for eligible organization-owned private repositories with GitHub Code Security
enabled. RepoInvariant fails locally with exit code `2` instead of producing an upload that GitHub
would reject when one SARIF run exceeds 25,000 results or the rendered report exceeds 10 MiB after
gzip compression. See GitHub's
[SARIF ingestion limits](https://docs.github.com/en/code-security/reference/code-scanning/sarif-files/sarif-support)
and
[SARIF upload documentation](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/integrate-with-existing-tools/upload-sarif-file).

### Fork pull-request boundary

The workflow uses `pull_request`, checks out untrusted fork content with persisted credentials
disabled, and never exposes a secret. GitHub downgrades write permissions for untrusted fork runs,
so the SARIF upload is also explicitly limited to pushes and same-repository pull requests. The
read-only scan and downloadable artifact still run for forks.

Do not change this workflow to `pull_request_target` merely to upload fork results. That event runs
in the privileged base-repository context and is unsafe when a job checks out or executes pull
request code. Review GitHub's
[secure-use guidance](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
before adding permissions or secrets.

## 4. Require the merge gate

Prefer a repository ruleset:

1. Open **Settings > Rules > Rulesets > New branch ruleset**.
2. Target `main` and any shared integration branch such as `develop`.
3. Require a pull request, resolved conversations, and the repository's chosen approval count.
4. Require status checks and select `RepoInvariant contracts` from a completed workflow run.
5. Require branches to be up to date if that matches the repository's merge policy.
6. Keep bypass access limited to an audited emergency group, then test the rule with a deliberate
   synthetic drift pull request.

For classic branch protection, configure the same check under **Settings > Branches**, but do not
configure both mechanisms with contradictory policies. GitHub documents the ruleset controls in
[About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets).

The required status check is the merge gate. Code Scanning upload is intentionally conditional and
must not be the required check because a fork run cannot safely receive repository write access.

## 5. Protect configuration and baselines

Commit a `.github/CODEOWNERS` rule that matches the paths used by the repository, for example:

```text
/.repoinvariant.yml                 @ORG/maintainers
/.repoinvariant-baseline.json       @ORG/maintainers
/.github/workflows/repoinvariant.yml @ORG/maintainers
```

Replace the placeholder owner and then enable **Require review from Code Owners**. Confirm the
pattern against GitHub's
[CODEOWNERS syntax](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners).

Baseline review checklist:

- Generate or refresh a baseline only from a trusted base-branch checkout.
- Review every accepted finding and the baseline scope digest change.
- Never let a fork workflow, bot with unreviewed input, or pull-request script rewrite the baseline.
- Require CODEOWNER approval for the config, baseline, and workflow paths.
- Run `repoinvariant doctor --strict --baseline .repoinvariant-baseline.json` and reject a scope
  mismatch.
- Regenerate after resolved findings so stale entries do not accumulate.

## Troubleshooting

- **No Code Scanning result:** verify repository eligibility, `security-events: write`, the
  same-repository condition, and the SARIF artifact before changing permissions.
- **Fork upload skipped:** expected; use annotations and the retained workflow artifact for review.
- **Required check not listed:** complete the workflow once on the target repository, then reopen
  the ruleset check selector.
- **Strict diagnosis fails:** run with `--verbose`, fix empty required ranges, or deliberately turn
  off unused rule families instead of preserving a false-green scanner.
