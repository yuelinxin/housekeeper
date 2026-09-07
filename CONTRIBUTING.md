# Contributing

Use English for code comments, documentation, UI strings, tests, commits, and pull
request descriptions. Keep product copy short and specific to the user's action.

Read [architecture](docs/architecture.md) and [testing](docs/testing.md) before changing
a provider. Add tests for behavior changes, especially ownership and transaction
boundaries. Do not test removal against a contributor's installed applications.

Use Python 3.10-compatible syntax. UI code must remain within GTK 4.12 and libadwaita
1.4 APIs. Follow the existing GtkBuilder and PyGObject conventions. Ruff handles
formatting and imports; mypy checks the UI-independent model and identity layer.

New installation providers must declare their available capabilities and degrade
independently. Source recognition alone must never authorize an uninstall. Include
fixtures for missing dependencies, ambiguous identities, and external state changes.

Keep changes reviewable: describe the user-visible problem, the resulting behavior,
and the validation performed. Record any untested platform behavior explicitly.
Do not raise the minimum supported runtime or widen native-removal support without
adding a tested compatibility matrix entry.
