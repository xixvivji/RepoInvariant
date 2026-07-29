# RepoInvariant

[![CI](https://github.com/xixvivji/RepoInvariant/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/xixvivji/RepoInvariant/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/repoinvariant.svg)](https://pypi.org/project/repoinvariant/)
[![Python](https://img.shields.io/pypi/pyversions/repoinvariant.svg)](https://pypi.org/project/repoinvariant/)
[![License](https://img.shields.io/pypi/l/repoinvariant.svg)](https://github.com/xixvivji/RepoInvariant/blob/main/LICENSE)

> Alpha: catch contract drift across repository artifacts before merge.

RepoInvariant is a deterministic CLI and GitHub Action that checks whether the contracts spread
across your repository still agree. It focuses on two expensive, repeatable failure modes:

- environment variables drifting between `.env.example`, Docker Compose, Kubernetes,
  GitHub Actions, and Spring configuration;
- requirement IDs disappearing between Markdown, OpenAPI `x-feature-id`, and tests.

It reports exact evidence instead of guessing intent. No API key, LLM, or source upload is
required.

## Status

RepoInvariant `v0.1.1` is a public alpha. The configuration and finding codes may change
before `v1.0.0`. Pin an exact commit SHA when using the GitHub Action.

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
repoinvariant init
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

## GitHub Action

The repository must be checked out before RepoInvariant runs. For supply-chain safety, pin an exact
commit SHA:

```yaml
permissions:
  contents: read

steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
  - uses: xixvivji/RepoInvariant@4f6d7c2b6fd171735f265df730c2eb743b8578af # v0.1.1
```

The action installs only the source bundled with the pinned action revision. It does not transmit
repository contents or require secrets.

| Input | Default | Description |
|---|---|---|
| `path` | `.` | Repository-relative directory to scan. |
| `config` | empty | Optional repository-relative configuration file. |
| `format` | `text` | `text`, `json`, `markdown`, or `sarif`. |
| `output` | empty | Optional repository-relative report path. |
| `fail-on` | `error` | Blocking threshold: `error` or `warning`. |
| `no-env` | `false` | Skip environment-contract checks. |
| `no-features` | `false` | Skip feature-traceability checks. |

Prefer rule-level severity configuration when only one check needs a temporary downgrade.

## Configuration

Run `repoinvariant init` to create `.repoinvariant.yml`:

```yaml
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

rules:
  ENV001: error
  ENV002: warning
  ENV003: warning
  TRACE001: error
  TRACE002: error
  TRACE003: error
  TRACE004: warning
```

`requirements_mode: definitions` counts IDs only when they look like canonical Markdown
definitions (headings, definition lists, and the first column of tables). Set it to `mentions` for
legacy repositories where any prose reference is authoritative.

The built-in `REQ-*` pattern is printed verbatim because it is a constrained public identifier
format. Matches from a custom `id_pattern` are reported as deterministic `custom-id-N` labels;
source locations remain exact, but arbitrary matched repository text never reaches logs or report
artifacts.

Each finding code can be set to `error`, `warning`, or `off`. This makes staged adoption explicit:
start a noisy rule as a warning, fix the baseline, then promote it to an error. Quote `"off"` when
editing YAML to avoid YAML 1.1 boolean parsing surprises.

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
| `ENV001` | A consumer uses an environment variable absent from the contract | error |
| `ENV002` | A contract variable has no discovered consumer | warning |
| `ENV003` | Explicit defaults disagree across artifacts | warning |
| `TRACE001` | A requirement ID is absent from the specification | error |
| `TRACE002` | A specification ID has no requirement | error |
| `TRACE003` | A specification ID has no test reference | error |
| `TRACE004` | A requirement appears to be defined more than once | warning |

## Design boundaries

RepoInvariant deliberately does not:

- decide whether two differently worded requirements mean the same thing;
- modify repository files automatically;
- compare live infrastructure or databases;
- print secret values found in configuration;
- claim full OpenAPI, Compose, Kubernetes, or Spring validation.

Use their native validators alongside RepoInvariant. RepoInvariant owns the gap **between** artifacts.

Configured files and report destinations must stay inside the repository. RepoInvariant rejects
configuration/output symlinks, limits configuration files to 256 KiB and scanned files to 2 MiB,
and fails closed on malformed configured YAML. Custom requirement patterns run with a timeout and
one shared matching-time budget. File reads and atomic report writes use no-follow directory
descriptors so a concurrent symlink swap cannot redirect them outside the repository.

## Roadmap

- [x] Environment contract checks
- [x] Requirement → OpenAPI → test traceability
- [x] Text, JSON, Markdown, and SARIF reports
- [x] Composite GitHub Action
- [ ] Version-baseline contracts across Gradle, Docker, CI, and documentation
- [ ] Reusable parser plugin API
- [x] PyPI trusted publishing and provenance-attested release automation
- [ ] Real-world compatibility fixtures from external projects

See [CONTRIBUTING.md](https://github.com/xixvivji/RepoInvariant/blob/main/CONTRIBUTING.md)
to help shape future releases. Participation is governed by the
[Code of Conduct](https://github.com/xixvivji/RepoInvariant/blob/main/CODE_OF_CONDUCT.md).

## License

Apache License 2.0. See [LICENSE](https://github.com/xixvivji/RepoInvariant/blob/main/LICENSE).
