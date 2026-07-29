# Changelog

All notable changes will be documented in this file. The project follows Semantic Versioning once
it reaches `v0.1.0`.

## [Unreleased]

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
