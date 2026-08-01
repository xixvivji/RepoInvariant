# Rule reference

RepoInvariant compares only evidence that its bounded, offline parsers recognize. This reference
defines the exact boundary of each finding; it is not a promise to validate every feature of the
underlying file formats.

| Rule | Invariant | Reference |
|---|---|---|
| `ENV001` | Every discovered environment consumer has a contract declaration | [ENV001](environment-contracts.md#env001) |
| `ENV002` | Every contract declaration has a discovered consumer | [ENV002](environment-contracts.md#env002) |
| `ENV003` | Explicit defaults for one environment variable agree | [ENV003](environment-contracts.md#env003) |
| `TRACE001` | Every requirement ID appears in the specification set | [TRACE001](feature-traceability.md#trace001) |
| `TRACE002` | Every specification ID appears in the requirement set | [TRACE002](feature-traceability.md#trace002) |
| `TRACE003` | Every specification ID appears in the configured test sources | [TRACE003](feature-traceability.md#trace003) |
| `TRACE004` | A requirement ID has at most one Markdown definition | [TRACE004](feature-traceability.md#trace004) |
| `VER001` | Every recognized static Java major equals the configured major | [VER001](java-version.md#ver001) |
| `VER002` | A recognized Java declaration resolves to one comparable major | [VER002](java-version.md#ver002) |
| `VER003` | Every required Java source group contains a recognized declaration | [VER003](java-version.md#ver003) |

## Severity and scanner controls

Each rule accepts `error`, `warning`, or `"off"`. Turning a rule off removes its findings; it does
not stop discovery or parsing. Unsafe paths, malformed configured YAML, unreadable input, and
resource-limit violations can therefore still produce command exit code `2`. Use `--no-env`,
`--no-features`, or `--no-versions` when the complete scanner must be skipped.

Quote `"off"` in YAML. RepoInvariant accepts the legacy YAML boolean `false` for compatibility,
but the quoted spelling behaves consistently across YAML 1.1 and YAML 1.2 editors.

## Finding and baseline identity

Findings are ordered deterministically. Messages and line/column positions are evidence, not
baseline identity. ENV and TRACE identity is stable when the same entity moves between files;
VER001 and VER002 identity includes the source file, so moving a declaration to another file makes
it new. VER003 identity is source-group-only. Serialized baselines store a fingerprint, code, and
severity; they do not store variable names, requirement IDs, messages, source paths, or environment
default values. A severity change requires a fresh review.

The environment and traceability references describe their rule-specific entity identity. Java
identity also includes its source group and, where applicable, source file and observed major.
The effective configuration and enabled scanner set are part of the separate baseline scope
digest. Explicitly selected experimental plugins publish namespaced codes such as
`sample.todo:TODO001`; their installed distribution version, declared rules, data-only
configuration, and severity overrides are also bound to that digest. See the
[parser plugin API](../parser-plugin-api.md) for its separate validation and trust boundary.
