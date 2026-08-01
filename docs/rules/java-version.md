# Java version contract rules

The Java version scanner is opt-in. When `versions.java` exists, RepoInvariant compares recognized
declarations with the quoted canonical major in `versions.java.expected`. It does not execute
Gradle, inspect remote container metadata, evaluate providers or matrices, or infer a version from
arbitrary prose.

## Recognized source boundary

| Source group | Recognized declaration | Not recognized |
|---|---|---|
| `gradle` | Code calls to `JavaLanguageVersion.of(...)` and `jvmToolchain(...)`, plus `sourceCompatibility` and `targetCompatibility` assignments whose value is a literal major or `JavaVersion.VERSION_*` | Comments/strings, Kotlin `jvmTarget`, wrappers, properties, catalogs, method calls that compute compatibility dynamically |
| `maven` | Direct `java.version`, `maven.compiler.release`, `maven.compiler.source`, and `maven.compiler.target` properties; literal `release`, `source`, and `target` configuration of `maven-compiler-plugin` | Profiles or CLI overrides, inherited parent values, property interpolation, arbitrary plugins, effective-POM resolution |
| `version_files` | First non-empty, non-comment declaration in a configured `.java-version`, including a canonical major/version or a distribution-prefixed version such as `openjdk64-21`, `graalvm64-17`, or `temurin-21` | Multiple declarations, shell expressions, arbitrary prose, or values without one identifiable Java major |
| `dockerfiles` | Line-local `FROM` for an allowlisted Java/build image, with simple literal `ARG` substitution declared before the first `FROM` | Arbitrary images, later `ARG`, line continuation, `ENV`, `RUN`, labels, stage aliases, remote manifests |
| `compose` | Scalar `services.*.image`, including YAML merge inheritance, when the image repository is allowlisted | `build`, Dockerfile args, commands, arbitrary images, and a fully dynamic image name such as `${IMAGE}` |
| `workflows` | Step-level `actions/setup-java@...` with `with.java-version` or `with.java-version-file` | Reusable/job-level workflows, setup-java forks, runner/container versions, and Java implied by Gradle |
| `docs` | `Java`, `Java version`, `JDK`, or `JDK version` labels using `:`/`=`, or the label in a leading table cell | General prose, headings, fenced code, HTML comments, and a Java label outside the first table cell |

Recognized Java image repository names are `amazoncorretto`, `corretto`, `eclipse-temurin`,
`eclipse-temurin-nightly`, `graalvm-ce`, `ibm-semeru-runtimes`, `java`, `jdk`, `jdk-community`,
`liberica-openjdk`, `liberica-openjdk-alpine`, `openjdk`, `sapmachine`, and `zulu-openjdk`, plus
names beginning `liberica-openjdk-` or `zulu-openjdk-`. The recognized build images are `gradle`
and `maven`; their tag must contain an identifiable Java marker such as `jdk17`, `temurin-21`,
`amazoncorretto-21`, or `ibm-semeru-21` to be static.

Allowlisting uses only the final repository path component. Registry and namespace are ignored, so
`ghcr.io/example/openjdk:21` is recognized as `openjdk:21`; image digest contents are not inspected.

Examples of supported structured documentation are:

```text
Java version: 21
- **Java version:** `21`
| Java | Eclipse Temurin 21.0.11+10 |
```

`Requires Java 21` is ordinary prose and is not a declaration. Ranges, multiple majors, providers,
workflow expressions, or an unresolved tag are recognized declarations but cannot resolve to one
comparable literal major.

Supported Gradle compatibility examples are `sourceCompatibility = 21`,
`targetCompatibility = "21"`, and `sourceCompatibility = JavaVersion.VERSION_21`. A provider or
computed expression is recognized as unresolved without retaining its contents. Maven values must
be direct element text. `${java.version}` inside compiler-plugin configuration is unresolved even
when the same POM contains a property; RepoInvariant does not construct an effective POM.

Configured `version_files` are scanned even when no workflow references them. When the same
`.java-version` is also selected by `actions/setup-java`, its mismatch is reported once as direct
version-file evidence while both source groups still satisfy `required` presence.

For distribution-prefixed version files, an explicit Java marker takes precedence over a later
distribution release number. For example, `graalvm-ce-java17-22.3.0` declares Java 17.

For workflow `java-version-file`, a fixed repository-local path is required. `.tool-versions` uses
the first `java <value>` entry; other files use the first token on the first non-empty, non-comment
line. Direct `version_files` use the complete first non-empty, non-comment line. A missing or empty
file yields an unresolved declaration. An existing target must be repository contained and
symlink-free; a missing leaf beneath a symlinked parent remains unresolved because no target is
opened.

## Required source groups

`versions.java.required` means the entire named source group must contain at least one recognized
declaration. It does not require every matching file to declare Java, and a glob match by itself is
not enough. Both mismatching and unresolved declarations satisfy presence. Optional groups are
still scanned and can produce VER001 or VER002; `required` only enables VER003.

## VER001

**Default severity:** `error` when `versions.java` is enabled

VER001 is emitted when a recognized declaration resolves to one static major that differs from
`versions.java.expected`.

```yaml
versions:
  java:
    expected: "21"
```

```kotlin
kotlin { jvmToolchain(17) }
```

Declarations with the same source group, file, and observed major are grouped into one finding;
additional positions are related locations. Align the declaration with the canonical major or
change the canonical target through review.

## VER002

**Default severity:** `warning` when `versions.java` is enabled

VER002 is emitted when RepoInvariant recognizes a Java declaration location but cannot resolve it
to one literal major. Examples include a Gradle provider, a workflow matrix expression, an
allowlisted image with an unresolved tag, and structured documentation containing a range or
multiple majors.

```kotlin
kotlin { jvmToolchain(javaTarget.get()) }
```

The unresolved source text is not retained or printed. Prefer a literal declaration where
practical. Unknown syntax and arbitrary images are different: they contribute no declaration and
produce VER003 only when their source group is required.

## VER003

**Default severity:** `warning` when `versions.java` is enabled

VER003 is emitted once for each required source group with no recognized static or unresolved
declaration anywhere in that group.

```yaml
versions:
  java:
    expected: "21"
    required: [gradle, workflows, docs]
```

Add a supported declaration to the missing group or remove the group from `required` if that
artifact class is intentionally absent. Because mismatching and unresolved evidence satisfies
presence, VER003 does not accompany VER001 or VER002 for the same source group.

## Failure, security, and scope boundary

Turning a VER rule off removes only its findings. Configured source files are still discovered and
parsed, so malformed YAML or Maven XML, XML document types/entities, invalid UTF-8, oversized
input, symlinks, repository escape, and bounded resource-limit violations remain command errors.
Use `--no-versions` to skip the scanner.

Each selected file is limited to 2 MiB, and one version scan is limited to 10,000 unique files,
64 MiB of aggregate UTF-8 input, 10,000 declarations per file, and 100,000 declarations overall.
Configured YAML and Maven XML also retain their 20,000-node and 100-level nesting limits. Exceeding
a resource limit returns command exit code `2`.

Scanning is deterministic and offline. Dynamic declaration contents are not written to findings or
baselines. RepoInvariant currently checks only the canonical Java major forms above; it does not
check Spring Boot, Gradle or Maven tool versions, resolve effective Maven POMs, compare Node or
Python runtimes, inspect Kubernetes runtime versions, parse non-GitHub CI systems, or infer from
arbitrary container images.
