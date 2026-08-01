"""Support ``python -m repoinvariant`` through the import-safe CLI bootstrap."""

from repoinvariant._entrypoint import main

raise SystemExit(main())
