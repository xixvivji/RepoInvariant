# RepoInvariant

> Alpha: catch contract drift across repository artifacts before merge.

RepoInvariant is a deterministic CLI and GitHub Action that checks whether the contracts spread
across your repository still agree. It focuses on two expensive, repeatable failure modes:

- environment variables drifting between `.env.example`, Docker Compose, Kubernetes,
  GitHub Actions, and Spring configuration;
- requirement IDs disappearing between Markdown, OpenAPI `x-feature-id`, and tests.

It reports exact evidence instead of guessing intent. No API key, LLM, or source upload is
required.

## Status

RepoInvariant `v0.1.0` is the first public alpha. The configuration and finding codes may change
before `v1.0.0`. Pin an exact commit SHA when using the GitHub Action.

## Quick start

Install the CLI from PyPI:

```bash
uv tool install repoinvariant

cd /path/to/your/repository
repoinvariant init
repoinvariant check .
```

During development, use:

```bash
uv sync --frozen --extra dev
uv run repoinvariant check examples/ticket-service
uv run pytest
```

A finding looks like this:

```text
compose.yml:12:7: error ENV001: DATABASE_URL is used but missing from the environment contract
docs/requirements.md:18:1: error TRACE001: REQ-HOLD-CREATE is missing from the specification
FAIL: 6 files, 2 errors, 0 warnings
```

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

## GitHub Action

The repository must be checked out before RepoInvariant runs. For supply-chain safety, pin an exact
commit SHA:

```yaml
permissions:
  contents: read

steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
  - uses: xixvivji/RepoInvariant@<commit-sha>
    with:
      path: .
      format: sarif
      output: repoinvariant-report.sarif
      no-features: "true" # optional during staged adoption
```

The action installs only the source bundled with the pinned action revision. It does not transmit
repository contents.

The action also accepts `no-env` and `no-features` boolean inputs. Prefer rule-level severity
configuration when only one check needs a temporary downgrade.

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

See [CONTRIBUTING.md](CONTRIBUTING.md) to help shape future releases.

## License

Apache License 2.0. See [LICENSE](LICENSE).
