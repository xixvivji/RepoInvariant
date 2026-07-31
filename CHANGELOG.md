# Changelog

All notable changes will be documented in this file. The project follows Semantic Versioning.

## [Unreleased]

## [0.4.0] - 2026-07-31

### Added

- A `doctor` command with text and JSON diagnostics for effective scanner state, rule severity,
  source coverage, ignored files, empty ranges, and adoption-baseline compatibility.
- Opt-in Java major-version contracts across Gradle toolchains, Dockerfiles, Compose images,
  `actions/setup-java`, and structured Markdown declarations.
- `VER001`, `VER002`, and `VER003` findings for mismatches, dynamic declarations, and missing
  required source declarations.
- A `no-versions` CLI and GitHub Action switch whose state is bound to adoption-baseline scope.
- Offline compatibility fixtures informed by three public repository layouts, with pinned
  source commits, license provenance, and deterministic mutation coverage.
- A documented security and validation gate for a future explicitly selected parser plugin API.
- A versioned Draft 2020-12 configuration JSON Schema with strict keys, runtime-aligned defaults,
  path and severity validation, and a YAML Language Server modeline in generated configurations.
- Rule-level reference documentation for every environment, traceability, and Java finding,
  including exact supported syntax, non-detection boundaries, privacy, and baseline identity.

### Changed

- Updated the documented stable Action pin to an immutable `v0.4.0` release source commit.

### Security

- Kept version contracts disabled unless explicitly configured, preserving existing v0.3 scan and
  baseline behavior.
- Bounded version evidence and repository-contained reads, rejected malformed configured YAML, and
  omitted unresolved expression contents from findings.
- Bounded YAML alias traversal, Docker `ARG` expansion, declaration counts, and repeated version
  file reads; escaped control characters in CLI diagnostics before writing workflow logs.

## [0.3.0] - 2026-07-29

### Added

- Privacy-preserving adoption baselines that snapshot existing findings and gate only new drift.
- An optional `baseline` GitHub Action input with filtered annotations, reports, and outputs.

### Changed

- Updated the documented stable Action pin to an immutable `v0.3.0` release source commit.

### Security

- Bound baselines to the effective scan scope and reject malformed, oversized, duplicate, or
  symlinked baseline files without retaining variable names, requirement identifiers, messages,
  or source paths.
- Gave opaque custom-pattern findings stable private identities so a new identifier cannot inherit
  an accepted display ordinal.
- Published new baseline files with atomic no-clobber semantics unless replacement is explicitly
  requested with `--force`.

## [0.2.0] - 2026-07-29

### Added

- Agent-oriented repository guidance and a safe usage-question issue form.
- Marketplace discovery badge and a complete copy-ready GitHub Actions workflow.
- Native GitHub annotations, Step Summary feedback, and reusable Action outputs.

### Changed

- Updated the documented Action pin to the exact `v0.1.1` tag commit.
- Made version prompts in issue forms resilient to future releases.
- Hardened Markdown and workflow-command rendering against untrusted finding content.

## [0.1.1] - 2026-07-29

### Added

- Passing and intentionally drifting ticket-service examples with regression coverage.
- Open-source community conduct guidance and package, CI, Python, and license badges.

### Changed

- Documented GitHub Action usage with an immutable release commit SHA and every supported input.
- Made contribution and license links render correctly outside GitHub.

## [0.1.0] - 2026-07-29

### Added

- Environment contract comparison across dotenv, Compose, Kubernetes, Actions, and Spring files.
- Requirement ID traceability across Markdown, OpenAPI, and test sources.
- Text, JSON, Markdown, and SARIF reports.
- Composite GitHub Action and an Apache-2.0 contribution baseline.
- Git Flow release branches, PyPI Trusted Publishing automation, and immutable workflow pins.
- Staged rule severities and definition-only requirement membership.
- Bounded, repository-contained file access and fail-closed YAML parsing.
- ReDoS and identifier-output protections for custom traceability patterns.
- Descriptor-relative file access that resists symlink-swap races.

### Changed

- Renamed the pre-alpha project, package, CLI, configuration file, and Action from `RepoTruth` to
  `RepoInvariant` before the first public release.
- Compose contracts now track host-side interpolation sources instead of container target names.
- GitHub Actions contracts now track `secrets.*` and `vars.*` sources, not literal `env` keys.
- Empty dotenv assignments are treated as unspecified defaults unless explicitly quoted.
- Custom traceability matches use opaque report labels and one aggregate regex time budget.
