# Parser plugin API: pre-v1 design gate

RepoInvariant does not yet execute third-party parser plugins. The Java version-contract parser and
the pinned compatibility fixtures are intentionally private proving grounds for the evidence model.
Publishing an API before those shapes settle would either freeze the wrong abstraction or give a
repository configuration an unexpected code-execution path.

## Proposed trust boundary

The first experimental API will load only an explicitly selected, already-installed Python entry
point from `repoinvariant.scanners.v1`. It will not:

- auto-load every installed plugin;
- activate code from `.repoinvariant.yml` alone;
- load a repository-local Python file or `module:object` path;
- install packages during a scan; or
- claim that an in-process plugin is sandboxed or timeout-safe.

Plugins are trusted Python code. A future hard isolation claim requires a separate worker process,
an enforceable timeout, and an operating-system-level filesystem and network boundary.

## Candidate API surface

The core is expected to own plugin IDs, rule namespaces, severity policy, output validation,
baseline fingerprints, and repository-contained reads. A plugin should receive a bounded
repository view rather than the repository root and should return immutable evidence only.

Before the API is marked experimental, tests must prove all of the following:

1. Plugins run only after explicit CLI selection and in deterministic ID order.
2. Missing, duplicate, incompatible, or malformed entry points fail closed with exit code 2.
3. Traversal, absolute paths, symlinks, special files, oversized files, and invalid UTF-8 are
   rejected by the core repository view.
4. Finding counts, text lengths, locations, related locations, and baseline keys are bounded and
   validated before any partial result is accepted.
5. Plugin selection, distribution version, declared rules, and plugin configuration are bound to
   the adoption-baseline scope.
6. Unexpected plugin exceptions do not disclose exception messages, repository content, or a
   traceback in reports.

The public roadmap item remains open until this contract has an end-to-end fixture distribution and
the isolation limitations are documented in the CLI and Action surfaces.
