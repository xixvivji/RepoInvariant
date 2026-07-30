# Feature traceability rules

The traceability scanner compares requirement IDs found in configured Markdown (`R`), IDs under a
configured specification extension (`S`), and textual IDs in configured test sources (`T`). It
does not infer semantic equivalence, execute tests, or require the specification input to be a
complete OpenAPI document.

## Requirement membership and definitions

The default identifier expression is `\bREQ-[A-Z0-9][A-Z0-9-]*\b`. Every match must also satisfy
the safety boundary: 2–128 characters, an ASCII letter first, only ASCII alphanumerics plus
`_.:-`, and at least one `-`, `_`, `.`, or `:`.

With `requirements_mode: definitions`, these Markdown forms define an ID:

```markdown
## REQ-LOGIN: Sign in

REQ-PROFILE — Update a profile

- **REQ-EXPORT**: Export records

| REQ-AUDIT | Audit events |

REQ-SETEXT
==========
```

The precise boundary is an ATX heading containing the ID; a leading-ID declaration or list item
with a non-empty body separated by `:`, `;`, an en/em dash, or `- `; the first cell of a
leading-pipe table; or a setext heading with only whitespace/emphasis before the ID. Fenced code,
HTML comments, and prose mentions do not define an ID. An inline link by itself is not a
definition, except when it appears in an ATX heading or a leading-pipe table's first cell, whose
broader definition forms take precedence.

With `requirements_mode: mentions`, every safe match outside fenced code and HTML comments joins
`R`. TRACE004 still counts only the definition forms above.

## Specification and test membership

In configured YAML or JSON specifications, RepoInvariant walks the parsed node tree and finds every
mapping key exactly equal to `features.openapi_extension`. Its value may be a string or nested list
of strings containing one or more IDs:

```yaml
paths:
  /sessions:
    post:
      x-feature-id: [REQ-LOGIN, REQ-AUDIT]
```

The extension may appear anywhere in the tree. Mapping, number, boolean, and null extension values
are ignored. Files are composed with a YAML parser, including files named `.json`; input that parser
cannot compose is a command error, but strict JSON syntax is not separately validated.

Configured test files are bounded UTF-8 text inputs. Any textual match counts, including comments,
docstrings, skipped tests, and dead code. RepoInvariant does not perform language parsing or test
discovery. A dynamically assembled string such as `"REQ-" + "LOGIN"` is not a match, and an ID
found only in tests does not produce a finding.

A custom `id_pattern` is evaluated once per visible Markdown line, once per specification string
scalar, and against the entire test-file text. Multiline constructs and regular-expression anchors
can therefore behave differently across the three source classes.

## TRACE001

**Default severity:** `error`

TRACE001 is emitted for each ID in `R - S`: a requirement belongs to the configured requirement
set but does not occur under the configured specification extension. Evidence points to its first
canonical requirement location.

```markdown
## REQ-EXPORT: Export records
```

With no matching specification extension value, this triggers TRACE001. Add the ID to the
specification, remove a stale requirement, or correct the configured extension and source globs.

## TRACE002

**Default severity:** `error`

TRACE002 is emitted for each ID in `S - R`: the specification declares the ID, but the configured
requirement membership does not contain it. Evidence points to its first specification location.

```yaml
x-feature-id: REQ-IMPORT
```

A prose mention does not satisfy the default `definitions` mode. Add a supported Markdown
definition, switch deliberately to `mentions`, or remove the orphan specification reference.
TRACE002 and TRACE003 can both describe the same specification ID.

## TRACE003

**Default severity:** `error`

TRACE003 is emitted for each ID in `S - T`: the specification declares the ID, but no configured
test text contains it. Evidence points to its first specification location.

Add a literal ID reference to a configured test source or remove an incorrect specification ID.
RepoInvariant checks traceability evidence, not whether that test executes or meaningfully verifies
the requirement.

## TRACE004

**Default severity:** `warning`

TRACE004 is emitted when one ID has at least two supported Markdown definition locations. The
second deterministic definition is primary evidence and the first is a related location.

```markdown
## REQ-AUDIT: Record access

| REQ-AUDIT | Audit every access |
```

Repeated prose mentions do not trigger this rule. Keep one canonical definition and replace other
definitions with links or ordinary references.

## Custom patterns, identity, and privacy

With the exact built-in pattern, public `REQ-*` IDs may appear in messages and the rule entity is
the ID. Any different `id_pattern` uses privacy mode: messages show a deterministic per-scan label
such as `custom-id-3`, while baseline identity uses a domain-separated SHA-256-derived entity. The
display ordinal can change when another ID sorts before it; the private baseline identity does not.

TRACE002 and TRACE003 can share the same underlying entity, but the rule code keeps their finding
fingerprints distinct. Serialized baselines store neither built-in nor custom IDs.

## Ignore and failure boundary

`features.ignore` excludes paths, not identifiers. Hidden path components, virtual environments,
VCS metadata, dependency directories, and binary files are excluded by built-in policy. Invalid
UTF-8, unreadable or oversized selected files, unsafe paths, malformed specifications, and custom
regular-expression budget violations are command errors. Turning a TRACE rule off removes only its
finding; `--no-features` skips the scanner.
