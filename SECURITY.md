# Security policy

## Supported versions

RepoInvariant `0.x` is alpha software. Security fixes are applied to the latest release and the
current `main` branch until the first stable compatibility policy is published.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature for this repository. Do not include
real credentials, production configuration, or private repository contents in a public issue.

Include the affected parser or report format, a minimal synthetic reproducer, the impact, and any
suggested mitigation. You should receive an acknowledgement within seven days.

## Data handling

RepoInvariant's built-in scanners read local files, produce local reports, and do not make network
requests during a scan. Built-in parsers never include discovered secret values in findings or
reports; variable names and source locations are sufficient evidence. Matches from custom
requirement patterns receive opaque labels rather than being copied into output.

An explicitly selected third-party plugin is trusted in-process code. It can access the filesystem,
environment, and network independently of the bounded `RepositoryView`, and it controls the text of
its finding messages and hints. The core validates evidence shape and redacts unexpected exceptions,
but it cannot determine whether plugin-authored text contains sensitive data. Install, pin, and
select only reviewed plugins whose data-handling policy is acceptable for the repository.

The installed CLI removes working-directory, scan-root, and `PYTHONPATH` import roots before
loading parser dependencies or a selected plugin, and fails closed when the selected entry-point
target was preloaded from one of those roots. This prevents repository-local dependency and plugin
module shadowing after RepoInvariant's bootstrap begins. Python resolves the `repoinvariant`
package itself before that bootstrap; use `python -I -m repoinvariant` for an untrusted initial
search path, or use the installed console script with `PYTHONPATH` unset.
