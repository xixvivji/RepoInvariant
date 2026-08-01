# Compatibility fixture notices

The three structural-adaptation fixtures in this directory are original, minimal test data created
for RepoInvariant. They model selected file layouts observed at immutable upstream commits; no
source text or other bytes were copied into those fixtures. They are not upstream snapshots and do
not by themselves establish syntax-level compatibility with those projects.

| Fixture | Reference project | Pinned revision | License |
| --- | --- | --- | --- |
| `spring-guide-gradle` | [spring-guides/gs-spring-boot-docker](https://github.com/spring-guides/gs-spring-boot-docker) | `8f42f5812e8b62bc31b092a8767a5073bbc786e0` | Apache-2.0 |
| `fineract-multimodule` | [apache/fineract](https://github.com/apache/fineract) | `c8b48ee8da3aaa135f7d327bf4e09bfa917e8c13` | Apache-2.0 |
| `testcontainers-library` | [testcontainers/testcontainers-java](https://github.com/testcontainers/testcontainers-java) | `2ac3c9773ca52381266463c3709c37155e190b68` | MIT |

`spring-petclinic-maven` is intentionally different: its `pom.xml` is a byte-for-byte copy of the
small `<properties>` range at lines 17-38 of
[spring-projects/spring-petclinic](https://github.com/spring-projects/spring-petclinic) commit
`88e37c15cf6fc8490b01bc3e8e2c800cec1ac272`, blob
`db5b9f78a5370deae4ace1192d5a8acf4b140d8e`. Its manifest records the copied range and SHA-256;
its `NOTICE` preserves attribution, and the Apache-2.0 license text is distributed in the
repository's top-level `LICENSE`. No other upstream bytes are included.

The project names and links identify the public repository shapes that motivated each fixture.
They do not imply endorsement by or affiliation with those projects.

Each fixture's `provenance.yml` records the exact observed paths and Git blob SHAs returned by the
GitHub Contents API at the pinned commit, plus a mapping from those paths to independently authored
fixture files. Paths marked `synthetic_extension` add an artifact class needed to exercise the
cross-file contract but do not claim an upstream counterpart. Tests verify this manifest and all
adapted local paths offline; CI never contacts or clones the upstream repositories.

## Immutable upstream syntax snapshots

The following fixtures contain byte-for-byte copies from the listed immutable revisions. Each
fixture bundles the applicable upstream license text as `LICENSE.upstream`, and its provenance
manifest records file length, Git blob SHA-1, and SHA-256. Project names identify provenance only
and do not imply endorsement or affiliation.

| Fixture | Upstream project | Pinned revision | License | Copied syntax |
| --- | --- | --- | --- | --- |
| `upstream-spring-petclinic` | [spring-projects/spring-petclinic](https://github.com/spring-projects/spring-petclinic) | `88e37c15cf6fc8490b01bc3e8e2c800cec1ac272` | Apache-2.0 | Spring `.properties` |
| `upstream-node-package-esm` | [nodejs/package-examples](https://github.com/nodejs/package-examples) | `01d632c10d89067a44c4c22b264b2c5a4effce5a` | MIT | `package.json`, ESM JavaScript |
| `upstream-python-flask` | [pallets/flask](https://github.com/pallets/flask) | `6a2f545bfd8ed31e19066a299296917e034aca58` | BSD-3-Clause | `pyproject.toml` |
| `upstream-kubernetes-kustomize` | [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes) | `a818af18fe29d999d6741234c8cd72709ef2f424` | Apache-2.0 | Kubernetes Deployment YAML |

RepoInvariant's built-in scanners exercise the Spring and Kubernetes snapshots. The Node and
Python snapshots preserve real, licensed syntax and test that `init --detect` currently rejects
repositories containing only those forms; standard-library parsing verifies snapshot integrity,
not RepoInvariant scanner compatibility. The Maven range described above is exercised by the Java
version-contract parser.

Tests consume only the checked-in bytes; routine test and scan runs never fetch upstream content.
