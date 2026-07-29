# Security policy

## Supported versions

RepoInvariant is pre-alpha. Security fixes are applied to the latest release and the current `main`
branch until the first stable compatibility policy is published.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature for this repository. Do not include
real credentials, production configuration, or private repository contents in a public issue.

Include the affected parser or report format, a minimal synthetic reproducer, the impact, and any
suggested mitigation. You should receive an acknowledgement within seven days.

## Data handling

RepoInvariant scans local files and produces local reports. It does not make network requests during a
scan. Parsers must never include discovered secret values in findings or reports; variable names
and source locations are sufficient evidence. Matches from custom requirement patterns receive
opaque labels rather than being copied into output.
