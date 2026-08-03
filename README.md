# RepoInvariant

[![CI](https://github.com/xixvivji/RepoInvariant/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/xixvivji/RepoInvariant/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/repoinvariant.svg)](https://pypi.org/project/repoinvariant/)
[![Python](https://img.shields.io/pypi/pyversions/repoinvariant.svg)](https://pypi.org/project/repoinvariant/)
[![License](https://img.shields.io/pypi/l/repoinvariant.svg)](https://github.com/xixvivji/RepoInvariant/blob/main/LICENSE)
[![GitHub Marketplace](https://img.shields.io/badge/Marketplace-RepoInvariant-2ea44f?logo=github)](https://github.com/marketplace/actions/repoinvariant)

> Alpha: catch contract drift across repository artifacts before merge.

RepoInvariant is a deterministic CLI and GitHub Action that checks whether the contracts spread
across your repository still agree. It focuses on three expensive, repeatable failure modes:

- environment variables drifting between `.env.example`, Docker Compose, Kubernetes,
  GitHub Actions, and Spring configuration;
- requirement IDs disappearing between Markdown, OpenAPI `x-feature-id`, and tests;
- a declared Java major drifting between Gradle/Maven build settings, `.java-version`, container
  images, CI, and structured documentation.

It reports exact evidence instead of guessing intent. No API key, LLM, or source upload is
required.

## Status

RepoInvariant `v0.5.1` is a public alpha. The configuration and finding codes may change
before `v1.0.0`. Pin an exact commit SHA when using the GitHub Action.

## GitHub Action

The repository must be checked out before RepoInvariant runs. For supply-chain safety, pin an exact
commit SHA:

```yaml
name: RepoInvariant

on:
  pull_request:

permissions:
  contents: read

jobs:
  contracts:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - name: Check out repository
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - name: Check repository contracts
        uses: xixvivji/RepoInvariant@13d76e9e43a308f9adc592376112df27a35ef90d # v0.5.1
```

The action installs only the source bundled with the pinned action revision. It does not transmit
repository contents or require secrets. For SARIF Code Scanning, required checks, branch rules,
fork safety, and baseline protection, follow the
[complete GitHub installation checklist](docs/github-installation.md).

| Input | Default | Description |
|---|---|---|
| `path` | `.` | Repository-relative directory to scan. |
| `config` | empty | Optional repository-relative configuration file. |
| `format` | `text` | `text`, `json`, `markdown`, or `sarif`. |
| `output` | empty | Optional repository-relative report path. |
| `baseline` | empty | Optional repository-relative adoption baseline. |
| `fail-on` | `error` | Blocking threshold: `error` or `warning`. |
| `strict` | `false` | Run `doctor --strict` before the contract check. |
| `no-env` | `false` | Skip environment-contract checks. |
| `no-features` | `false` | Skip feature-traceability checks. |
| `no-versions` | `false` | Skip configured Java version-contract checks. |

Prefer rule-level severity configuration when only one check needs a temporary downgrade.

## Quick start

Install the CLI from PyPI:

```bash
# uv (recommended)
uv tool install repoinvariant

# pipx
pipx install repoinvariant

# pip (inside an activated virtual environment)
python -m pip install repoinvariant
```

Then initialize and scan a repository:

```bash
cd /path/to/your/repository
repoinvariant init --detect
repoinvariant doctor . --strict --verbose
repoinvariant check .
```

## See it catch drift

The repository includes a passing
[ticket-service example](https://github.com/xixvivji/RepoInvariant/tree/main/examples/ticket-service)
and an
[intentionally broken copy](https://github.com/xixvivji/RepoInvariant/tree/main/examples/ticket-service-drift).
Clone the repository and run both to see the merge gate turn red without configuring a real
project first:

```console
$ repoinvariant check examples/ticket-service
PASS: 6 files, 0 errors, 0 warnings

$ repoinvariant check examples/ticket-service-drift
compose.yml:7:25: error ENV001: Environment variable 'HOLD_TTL_SECONDS' is consumed but missing from the contract.
  hint: Declare the variable in an environment contract or explicitly ignore it.
openapi.yml:8:21: error TRACE003: Specification feature 'REQ-HOLD-CREATE' has no matching test.
  hint: Reference REQ-HOLD-CREATE in a configured test file.
FAIL: 6 files, 2 errors, 0 warnings
```

The drift example breaks two contracts on purpose: Compose consumes `HOLD_TTL_SECONDS` without
declaring it in `.env.example`, and the OpenAPI operation has no matching requirement ID in its
test. Add `HOLD_TTL_SECONDS=300` to the environment contract and `REQ-HOLD-CREATE` to the test to
make it pass.

## Configuration

The versioned
[JSON Schema](https://github.com/xixvivji/RepoInvariant/blob/main/schemas/repoinvariant-config-v1.schema.json)
provides IDE completion and catches unknown keys, invalid severities, unsafe path patterns, and
malformed Java contract settings before a scan. `repoinvariant init` creates
`.repoinvariant.yml` with the YAML Language Server modeline already attached:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/xixvivji/RepoInvariant/main/schemas/repoinvariant-config-v1.schema.json
version: 1

env:
  contracts: [.env.example]
  compose: [compose*.yml, docker-compose*.yml]
  kubernetes: [k8s/**/*.yml]
  workflows: [.github/workflows/*.yml]
  spring:
    - src/main/resources/application*.yml
    - src/main/resources/application*.properties
  ignore: [CI, HOME, PATH, GITHUB_*, RUNNER_*]

features:
  requirements: [docs/**/*.md]
  specifications: [openapi*.yml, docs/openapi*.yml]
  tests: [tests/**/*, src/test/**/*]
  id_pattern: '\bREQ-[A-Z0-9][A-Z0-9-]*\b'
  openapi_extension: x-feature-id
  requirements_mode: definitions
  ignore: []

# Optional: omit this section when the repository has no single Java target.
versions:
  java:
    expected: "21"
    gradle: ["**/build.gradle", "**/build.gradle.kts"]
    maven: ["**/pom.xml"]
    version_files: ["**/.java-version"]
    dockerfiles: ["**/Dockerfile", "**/Dockerfile.*"]
    compose:
      - "**/compose*.yml"
      - "**/compose*.yaml"
      - "**/docker-compose*.yml"
      - "**/docker-compose*.yaml"
    workflows: [.github/workflows/*.yml, .github/workflows/*.yaml]
    docs: [README.md, docs/**/*.md]
    ignore: [examples/legacy/**]
    required: [gradle, maven, version_files, workflows, docs]

rules:
  ENV001: error
  ENV002: warning
  ENV003: warning
  TRACE001: error
  TRACE002: error
  TRACE003: error
  TRACE004: warning
  VER001: error
  VER002: warning
  VER003: warning
```

Keep that comment when renaming a custom `--config` file. The Schema describes the user-authored
configuration before defaults are merged; all sections are optional except that an enabled
`versions` section requires `java.expected`. A configured list replaces its built-in list instead
of appending to it, so include every path pattern that must remain in scope.

Schema validation complements the CLI. Duplicate YAML keys, unsupported YAML merge keys (`<<`),
recursive aliases, file-size and node budgets, Python regular-expression compilation, filesystem
containment, and symlink checks require runtime context and remain enforced by
`repoinvariant check` and `repoinvariant doctor`. JSON Schema also treats `1.0` as an integer
value, while RepoInvariant deliberately accepts only the exact YAML integer `version: 1`. Quote
values such as `"on"`, `"off"`, `"yes"`, `"no"`, and date-shaped strings when they are intended
to be ordinary strings so YAML 1.1 and 1.2 tools agree.

`requirements_mode: definitions` counts IDs only in supported canonical Markdown forms: headings,
leading-ID declarations or list items, leading-pipe table first cells, and setext headings. Set it
to `mentions` for legacy repositories where any prose reference is authoritative.

The built-in `REQ-*` pattern is printed verbatim because it is a constrained public identifier
format. Matches from a custom `id_pattern` are reported as deterministic `custom-id-N` labels;
source locations remain exact, but arbitrary matched repository text never reaches logs or report
artifacts.

`versions.java.expected` is an opt-in canonical Java major, not an adoption baseline. It must be a
quoted major such as `"17"` or `"21"`. RepoInvariant recognizes literal Gradle toolchains and
compatibility declarations, Maven compiler properties/configuration, `.java-version`, known Java
Docker and Compose image tags, `actions/setup-java`, and structured Markdown declarations. Dynamic
expressions are reported without printing their contents. Add a source name to `required` only when
that artifact class must declare the version.

Each finding code can be set to `error`, `warning`, or `off`. This makes staged adoption explicit:
start a noisy rule as a warning, reduce the accepted backlog, then promote it to an error. Quote
`"off"` when editing YAML to avoid YAML 1.1 boolean parsing surprises.

## Diagnose scan scope

Use `doctor` to inspect the effective scan before making `check` a merge gate:

```text
repoinvariant doctor [path] [--config FILE] [--baseline FILE]
                       [--format text|json] [--verbose] [--strict]
                       [--plugin ID ...]
                       [--no-env] [--no-features] [--no-versions]
```

```bash
repoinvariant doctor .
repoinvariant doctor . --strict --verbose
repoinvariant doctor . --format json
```

The diagnosis shows scanner state, rule severities (including `off` rules), configured source
coverage, empty source ranges, ignored-file counts, and optional baseline compatibility. It uses
the same effective configuration and scanner switches as `check`. The default text and JSON
outputs report counts and statuses. `--verbose` additionally reports bounded, deterministic,
repository-relative lists of matched and ignored scan paths. Each collection lists at most 50
items and the complete report lists at most 1,000; exact counts and omitted counts remain visible.

An ignored file is one that first matched a configured source glob and was then excluded by a
configured or built-in ignore rule, or identified as a binary traceability input. `doctor` does
not walk the rest of the repository merely to list files that were never in scan scope. It also
never prints file contents, discovered secret values, custom identifier matches, or baseline hash
values.

Without `--strict`, a completed diagnosis returns exit code `0`, including when a source range is
empty, a rule is `off`, or a scanner is disabled. `--strict` returns exit code `1` when an effective
scanner scans no files or a required Java source group contains no recognized declaration. Invalid
configuration or baseline data and unsafe scanner input return exit code `2`. `check` remains the
contract-finding merge gate; strict doctor validates that the intended gate actually has coverage.

## Adopt an existing repository

```text
repoinvariant baseline [path] [--config FILE] [--output FILE] [--force]
                         [--plugin ID ...]
                         [--no-env] [--no-features] [--no-versions]
```

If an established repository already has findings that cannot all be fixed at once, snapshot the
current set and gate only newly introduced drift:

```bash
repoinvariant baseline .
git add .repoinvariant-baseline.json
repoinvariant check . --baseline .repoinvariant-baseline.json
```

`repoinvariant baseline` returns exit code `0` after a successful scan and write even when it
records blocking findings. A check using that baseline suppresses matching rule/entity/severity
identities from reports, GitHub annotations, and the exit decision; new identities still behave
normally. Messages and source locations are evidence, not identity, so moving the same violation
does not make it new. Resolved entries become non-blocking `stale` entries. Remove them promptly:
if an identical violation returns while its stale entry remains, it is still accepted.

The baseline is bound to the effective configuration and enabled scanner set. Use the same
`--config`, `--no-env`, `--no-features`, and `--no-versions` options for generation and checking.
RepoInvariant returns exit code `2` on a scope mismatch instead of silently applying an
incompatible baseline.
For the GitHub Action, add the optional input (default: empty):

```yaml
with:
  baseline: .repoinvariant-baseline.json
```

A baseline is a reviewed allowlist, so generate it from a trusted default branch, commit it, and
protect changes with `CODEOWNERS` or separate approval. Never regenerate it blindly from an
untrusted pull-request branch. The file stores versioned hashes plus finding codes and severities;
it does not store variable names, requirement identifiers, messages, or source paths.

Before using `baseline --force`, run a baseline-free `check`, review every current finding, and
inspect the baseline diff. Regeneration accepts all findings visible in that scan, including newly
introduced ones.

Then run one of the stable report formats:

```bash
repoinvariant check . --format text
repoinvariant check . --format json --output repoinvariant-report.json
repoinvariant check . --format markdown --output repoinvariant-report.md
repoinvariant check . --format sarif --output repoinvariant-report.sarif
```

Exit code `0` means no blocking drift, `1` means a configured contract failed, and `2` means
the command or configuration was invalid. Add `--fail-on warning` for a stricter merge gate.

## Finding codes

| Code | Meaning | Default severity |
|---|---|---|
| [`ENV001`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/environment-contracts.md#env001) | A consumer uses an environment variable absent from the contract | error |
| [`ENV002`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/environment-contracts.md#env002) | A contract variable has no discovered consumer | warning |
| [`ENV003`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/environment-contracts.md#env003) | Explicit defaults disagree across artifacts | warning |
| [`TRACE001`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/feature-traceability.md#trace001) | A requirement ID is absent from the specification | error |
| [`TRACE002`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/feature-traceability.md#trace002) | A specification ID has no requirement | error |
| [`TRACE003`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/feature-traceability.md#trace003) | A specification ID has no test reference | error |
| [`TRACE004`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/feature-traceability.md#trace004) | A requirement appears to be defined more than once | warning |
| [`VER001`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/java-version.md#ver001) | A recognized static Java declaration differs from `versions.java.expected` | error |
| [`VER002`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/java-version.md#ver002) | A recognized Java declaration cannot resolve to one comparable literal major | warning |
| [`VER003`](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/java-version.md#ver003) | A required source group has no recognized Java declaration | warning |

See the
[complete rule index](https://github.com/xixvivji/RepoInvariant/blob/main/docs/rules/README.md)
for scanner controls, baseline identity, and the exact supported and unsupported syntax for every
source.

## Design boundaries

RepoInvariant deliberately does not:

- decide whether two differently worded requirements mean the same thing;
- modify repository files automatically;
- compare live infrastructure or databases;
- print secret values found by its built-in scanners;
- claim full OpenAPI, Compose, Kubernetes, or Spring validation;
- execute Gradle, resolve workflow matrices, or infer Java versions from arbitrary image names and
  prose.

Use their native validators alongside RepoInvariant. RepoInvariant owns the gap **between** artifacts.

Configured files, baselines, and report destinations must stay inside the repository.
RepoInvariant rejects their symlinks, limits configuration files to 256 KiB and scanned or
baseline files to 2 MiB, and fails closed on malformed configured YAML or baseline JSON. Custom
requirement patterns run with a timeout and one shared matching-time budget. File reads and atomic
writes use no-follow directory descriptors so a concurrent symlink swap cannot redirect them
outside the repository.

## Compatibility fixtures and parser extensions

Parser compatibility is exercised offline against both independently authored structural fixtures
and licensed byte-for-byte syntax snapshots. The built-in scanners consume the Spring properties,
Kubernetes manifest, and Maven compiler-properties snapshots. Node ESM/package metadata and Python
project metadata are provenance fixtures that explicitly assert the current unsupported
`init --detect` boundary; their presence does not claim Node or Python scanner support. Every copied
file or range is tied to an immutable upstream commit and blob, records its length and SHA-256, and
carries the applicable attribution or license. CI never fetches upstream content. See
[`tests/fixtures/compatibility/THIRD_PARTY_NOTICES.md`](tests/fixtures/compatibility/THIRD_PARTY_NOTICES.md)
for exact provenance and the compatibility boundary.

Trusted installed scanners can extend RepoInvariant through the experimental
`repoinvariant.scanners.v1` entry-point API. Plugins remain inert until explicitly selected with a
repeatable `--plugin ID` on `check`, `baseline`, or `doctor`; configuration alone never activates
code. The core provides a bounded no-follow repository view, validates and namespaces all plugin
evidence, redacts unexpected failures, and binds selected plugin identity and configuration to
adoption baselines. Plugins run in-process and are not sandboxed, so install only reviewed,
version-pinned distributions. A selected plugin can access the process, filesystem, environment,
and network, and the core cannot determine whether plugin-authored finding text contains sensitive
data. See the complete
[`parser plugin API`](docs/parser-plugin-api.md) contract and sample distribution.

## Roadmap

- [x] Environment contract checks
- [x] Requirement → OpenAPI → test traceability
- [x] Text, JSON, Markdown, and SARIF reports
- [x] Composite GitHub Action
- [x] Java version contracts across Gradle, Maven, `.java-version`, Docker, CI, and documentation
- [x] Experimental reusable parser plugin API
- [x] PyPI trusted publishing and provenance-attested release automation
- [x] Licensed upstream-syntax compatibility snapshots from external projects

See [CONTRIBUTING.md](https://github.com/xixvivji/RepoInvariant/blob/main/CONTRIBUTING.md)
to help shape future releases. Participation is governed by the
[Code of Conduct](https://github.com/xixvivji/RepoInvariant/blob/main/CODE_OF_CONDUCT.md).
For usage help, open a
[question](https://github.com/xixvivji/RepoInvariant/issues/new?template=question.yml) with a
minimal synthetic example.

## License

Apache License 2.0. See [LICENSE](https://github.com/xixvivji/RepoInvariant/blob/main/LICENSE).
