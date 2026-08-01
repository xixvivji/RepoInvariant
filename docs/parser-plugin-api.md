# Experimental parser plugin API

RepoInvariant can run trusted, already-installed Python scanner plugins through the versioned
`repoinvariant.scanners.v1` entry-point group. The API is experimental before `v1.0.0`: its
Python types and validation limits may change between minor releases.

## Explicit activation

A plugin is imported and loaded only when its entry-point ID is selected on `check`, `baseline`, or
`doctor`:

```console
repoinvariant check . --plugin sample.todo
repoinvariant baseline . --plugin sample.todo
repoinvariant doctor . --baseline .repoinvariant-baseline.json --plugin sample.todo
```

`--plugin` is repeatable. RepoInvariant rejects duplicate or unsafe IDs, sorts selected IDs, and
loads exactly one matching entry point for each ID. Missing entry points, duplicate installed
providers, incompatible API versions, malformed declarations, loading failures, and scan failures
return command exit code `2`.

`doctor --plugin` imports the selected entry point, validates the loaded scanner metadata, and
includes the plugin identity in baseline-scope compatibility. Import-time and loader code therefore
runs, but `doctor` does not call the scanner's `scan()` method or add plugin targets to the core
scanner inventory; use `check` to exercise plugin discovery and evidence.

The following never activates or obtains a plugin:

- installation alone;
- a `plugins` section in `.repoinvariant.yml` without `--plugin`;
- a repository-local Python file or a `module:object` CLI value;
- package installation during a scan; or
- an entry point in any group other than `repoinvariant.scanners.v1`.

The composite GitHub Action does not install plugin distributions and has no plugin input. Use the
CLI in a workflow step whose trusted environment installs the exact plugin distribution, then pass
an explicit `--plugin` ID. Pin both the Action/CLI and plugin distribution in that workflow.

The installed CLI bootstrap removes the working directory and `PYTHONPATH` roots from Python's
module search path before importing RepoInvariant's parser dependencies. Once the scan root is
resolved, plugin loading also removes repository-contained import roots, checks the selected entry
point's target module and parent packages, and rejects a target already loaded from the repository
or `PYTHONPATH`. A repository package visible through its direct parent is removed with a
target-specific check; a target visible only through a broader runtime root fails closed instead
of removing that root. These path changes remain in effect for lazy plugin imports during the scan.

Python must select the `repoinvariant` package itself before any package-owned bootstrap can run.
For an untrusted working tree or environment, use an isolated interpreter invocation such as
`python -I -m repoinvariant ...`, or invoke the installed `repoinvariant` console script with
`PYTHONPATH` unset. Do not run a repository-local launcher or put an untrusted package named
`repoinvariant` on the initial module search path.

## Distribution and scanner contract

An installed distribution declares an entry point whose name equals the scanner's `plugin_id`:

```toml
[project.entry-points."repoinvariant.scanners.v1"]
"sample.todo" = "repoinvariant_sample_plugin:scanner"
```

The loaded object has four members:

```python
import hashlib

from repoinvariant.models import Severity
from repoinvariant.plugin_api import PluginFinding, PluginLocation, PluginRule


class TodoScanner:
    api_version = 1
    plugin_id = "sample.todo"
    rules = (
        PluginRule(
            code="TODO001",
            default_severity=Severity.WARNING,
            description="A configured marker is present.",
        ),
    )

    def scan(self, repository, config):
        findings = []
        for path in repository.files(config.get("patterns", ("**/*.todo",))):
            for line_number, line in enumerate(repository.read_text(path).splitlines(), 1):
                if line.strip() == config.get("marker", "TODO"):
                    entity = hashlib.sha256(
                        f"{path}\0{line_number}".encode()
                    ).hexdigest()
                    findings.append(
                        PluginFinding(
                            rule="TODO001",
                            message="A configured marker is present.",
                            baseline_key=f"todo:{entity}",
                            location=PluginLocation(path, line_number, 1),
                        )
                    )
        return tuple(findings)


scanner = TodoScanner()
```

`api_version` must be the integer `1`. `rules` and the scan result must be tuples of the immutable
public API dataclasses. Local rule codes are uppercase identifiers. The core publishes findings as
`<plugin_id>:<local_rule>`, owns the configured severity, and rejects unknown or duplicate rules.

## Data-only configuration

Repository configuration does not select code. It supplies bounded JSON-compatible data and local
rule overrides only after the matching CLI ID is selected:

```yaml
plugins:
  sample.todo:
    config:
      marker: TODO
      patterns: ["**/*.todo"]
    rules:
      TODO001: warning
```

Configuration mappings are passed as immutable views; lists become tuples. Strings are limited to
4,096 characters, a list or mapping to 1,024 entries, and mapping keys to 128 non-empty characters.
Runtime validation additionally requires finite numbers and limits plugin configuration to 10,000
total values and 32 nesting levels; those aggregate/depth checks are not expressible in the JSON
Schema. Rule values are `error`, `warning`, or `"off"`.

## Bounded repository view

Plugins receive `RepositoryView`, not the repository root. Its public operations are:

- `files(patterns) -> tuple[str, ...]` for stable repository-relative regular-file discovery; and
- `read_text(path) -> str` for no-follow UTF-8 reads.

The view rejects traversal, absolute or backslash paths, symlinks in any component, special files,
invalid UTF-8, files above 2 MiB, more than 10,000 discovered/read files, and more than 32 MiB read
by one plugin invocation. Returned finding and related locations must name a file discovered or
read through that same view.

## Evidence validation and baselines

No plugin result is accepted until the complete immutable tuple passes core validation. Per scan,
the core permits at most 10,000 plugin findings; each plugin may declare at most 128 rules. Messages
and hints are single-line strings up to 2,048 characters. Baseline keys are safe identifiers up to
512 characters. Their alphabet excludes spaces and many path characters; hash arbitrary entity
text or repository paths into a deterministic hexadecimal identifier instead of copying them into
the key. Locations are positive bounded integers, paths are repository relative, and each finding
has at most 32 related locations. Duplicate rule/baseline identities fail closed.

Adoption-baseline scope includes, in deterministic ID order:

- selected plugin IDs;
- distribution names and versions;
- API version and declared rules/default severities;
- selected plugin configuration; and
- rule severity overrides.

Changing any of them makes an existing baseline incompatible. Plugin finding codes and hashed
fingerprints are stored in the baseline; plugin messages, source paths, and raw baseline keys are
not.

## Exception and isolation boundary

Unexpected load or scan exceptions are replaced with a fixed error naming only the selected plugin
ID. Exception messages, repository contents, and tracebacks are not placed in reports or normal CLI
errors.

Finding messages and hints are plugin-authored output. The core validates their type, length, and
control characters, but cannot infer whether their text contains a credential or other sensitive
repository value. Plugin authors must emit structural evidence rather than copied values, and users
must treat plugin output handling as part of the plugin's trust review.

Plugins remain trusted in-process Python code. `RepositoryView` is a safe API boundary, not a
sandbox: plugin code can use Python or native libraries to access the process, filesystem, network,
environment, or unbounded CPU. RepoInvariant does not provide a worker process, OS isolation, or a
timeout. Import-path isolation prevents accidental repository/PYTHONPATH shadowing before the
selected loader runs; it does not constrain a trusted plugin after execution begins. Install and
select only reviewed, version-pinned distributions. A future hard-isolation claim requires a
separate process plus enforceable filesystem, network, memory, and time limits.

The repository includes an installed-layout sample distribution and end-to-end tests covering all
six experimental gates: explicit deterministic selection; entry-point failures; bounded reads;
evidence validation; baseline scope; and exception redaction.
