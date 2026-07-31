# Compatibility fixture notices

The fixtures in this directory are original, minimal test data created for RepoInvariant. They
model selected file layouts observed at immutable upstream
commits. No source text or other bytes were copied from the referenced projects. They are not
upstream snapshots and do not by themselves establish syntax-level compatibility with those
projects.

| Fixture | Reference project | Pinned revision | License |
| --- | --- | --- | --- |
| `spring-guide-gradle` | [spring-guides/gs-spring-boot-docker](https://github.com/spring-guides/gs-spring-boot-docker) | `8f42f5812e8b62bc31b092a8767a5073bbc786e0` | Apache-2.0 |
| `fineract-multimodule` | [apache/fineract](https://github.com/apache/fineract) | `c8b48ee8da3aaa135f7d327bf4e09bfa917e8c13` | Apache-2.0 |
| `testcontainers-library` | [testcontainers/testcontainers-java](https://github.com/testcontainers/testcontainers-java) | `2ac3c9773ca52381266463c3709c37155e190b68` | MIT |

The project names and links identify the public repository shapes that motivated each fixture.
They do not imply endorsement by or affiliation with those projects.

Each fixture's `provenance.yml` records the exact observed paths and Git blob SHAs returned by the
GitHub Contents API at the pinned commit, plus a mapping from those paths to independently authored
fixture files. Paths marked `synthetic_extension` add an artifact class needed to exercise the
cross-file contract but do not claim an upstream counterpart. Tests verify this manifest and all
adapted local paths offline; CI never contacts or clones the upstream repositories.
