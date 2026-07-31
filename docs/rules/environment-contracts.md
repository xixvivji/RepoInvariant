# Environment contract rules

The environment scanner builds a contract set `C`, a consumer set `U`, and the set of explicit
default values `D(name)` for each variable after applying `env.ignore`. It compares names and
privacy-preserving default digests; discovered default values are never written to findings,
reports, or baselines.

## Recognized evidence

| Source | What counts | Important exclusions |
|---|---|---|
| Contract files | Dotenv names matching `[A-Za-z_][A-Za-z0-9_]*`, with optional `export` and assignment | Shell commands, invalid names, escape processing, and multiline dotenv syntax |
| Compose | `$NAME`, `${NAME}` and Compose operators in YAML scalars; bare `environment` entries; repository-local `env_file` | Single-quoted interpolation, `$${ESCAPED}`, dynamic `env_file`, and nested interpolation |
| Kubernetes | `containers` or `initContainers` `env[].name`; literal `env[].value`; exact `${NAME}` in YAML scalars | `envFrom`, Kubernetes `$(NAME)`, operator placeholders, and Kubernetes schema validation |
| GitHub Actions | Case-sensitive `secrets.NAME` and `vars.NAME` references in YAML scalars | `env.NAME`, bracket notation, dynamic indexing, and literal `env:` keys |
| Spring | `${NAME}` and `${NAME:default}` in selected YAML scalars and `.properties` lines | SpEL, `$NAME`, nested placeholders, properties continuation, and escape semantics |

RepoInvariant parses every selected Compose scalar, not only service environment fields, and every
selected workflow scalar, not only expressions. This deliberately favors simple repository-wide
evidence and can count an example or description string. Kubernetes input is similarly located by
tree shape rather than by validating a Kubernetes object schema.

Contract assignments have these boundaries:

```dotenv
REQUIRED
export TOKEN
PORT=8080
UNSPECIFIED=
EXPLICIT_EMPTY=""
```

`NAME=` declares a variable without an explicit default. Quoted empty strings are explicit empty
defaults. RepoInvariant removes one outer quote pair but does not implement a full shell or dotenv
interpreter.

Compose `env_file` paths are resolved relative to the Compose file and must remain inside the
repository. A static existing file is read as dotenv consumer evidence. A dynamic path is skipped.
A missing static path, repository escape, or symbolic link is a command error.

## Explicit default comparison

The following values participate in `D(name)`:

- non-empty dotenv assignments and quoted empty dotenv assignments;
- Compose `${NAME:-value}` and `${NAME-value}` fallbacks;
- Compose `env_file` assignments with a non-empty raw value, including quoted empty values;
- non-null Kubernetes `env[].value` scalars that do not contain exact `${NAME}` syntax;
- Spring `${NAME:value}` fallbacks, including an empty fallback.

Bare references, dotenv `NAME=`, Compose required or alternate operators, Kubernetes `valueFrom`,
exact `${OTHER}` Kubernetes values, and workflow references do not add a default. Unsupported
Kubernetes syntax such as `$(OTHER)` is still an ordinary literal scalar for default comparison and
can contribute to ENV003. Textually equal Compose `:-` and `-` fallbacks compare equal even though
Compose gives the operators different runtime semantics.

`env.ignore` patterns apply to both environment names and selected paths. Prefer clearly
name-shaped patterns such as `GITHUB_*` and clearly path-shaped patterns such as `runtime.env` to
avoid an accidental cross-match.

## ENV001

**Default severity:** `error`

ENV001 is emitted for every name in `U - C`: at least one recognized consumer exists, but no
configured contract file declares the name. The primary location is the first deterministic
consumer location.

```yaml
# compose.yml
services:
  app:
    environment:
      APP_PORT: ${APP_PORT}
```

With no `APP_PORT` entry in a selected contract file, this triggers ENV001. Add the declaration to
the canonical contract or explicitly ignore the name. A literal target such as
`APP_PORT: "8080"` is not external consumption and does not trigger the rule.

The baseline entity is derived from the variable name. ENV001 can coexist with ENV003 when
uncontracted consumers also declare conflicting fallbacks.

## ENV002

**Default severity:** `warning`

ENV002 is emitted for every name in `C - U`: the contract declares the name, but no supported
consumer syntax references it. The primary location is the first deterministic contract
declaration.

```dotenv
LEGACY_ENDPOINT
```

Remove stale declarations or add a supported consumer. A consumer in an unconfigured file or an
unsupported form such as `secrets['LEGACY_ENDPOINT']` does not satisfy this rule. The baseline
entity is derived from the variable name.

## ENV003

**Default severity:** `warning`

ENV003 is emitted when `D(name)` contains more than one distinct normalized value digest. It checks
explicit fallback text, not full runtime equivalence.

```dotenv
APP_PORT=8080
```

```yaml
services:
  app:
    environment:
      APP_PORT: ${APP_PORT:-9090}
```

This triggers ENV003 without exposing either value in the finding. Align the explicit defaults or
remove a duplicate source of truth. Reference and default forms explicitly excluded above are
absent from the comparison, while textually different values can warn even if an application would
interpret them equivalently. The baseline entity is derived from the variable name.

## Failure and ignore boundary

`rules.ENV00x: "off"` suppresses only that finding. It does not make malformed configured YAML,
unsafe `env_file` paths, oversized files, invalid UTF-8, or symlinks acceptable. `--no-env` skips
the entire environment scanner. Configured source globs and `env.ignore` bound which files can
contribute evidence; unlike feature traceability, this scanner has no built-in directory-ignore
list.
