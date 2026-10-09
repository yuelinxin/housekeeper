# Changelog

## 0.2.1 — 2026-10-08

- Apply custom application icons to all of an app's launchers and unique named
  icons across GNOME's app grid, docks and task panels, regardless of installation
  source. Preserve the user's icon theme through inheritance, restore originals,
  avoid shared names, and roll back failed multi-launcher changes. When the icon
  theme setting is locked or unavailable, still change the launchers.
- Release the custom names of uninstalled apps after inventory changes, and keep
  Restore Original Icon available while an app's custom names remain in use.
- Preserve embedded AppImage desktop identities when an optional SquashFS reader
  is available, and repair missing window classes on older imports during icon edits.
  When the embedded desktop ID is already installed, such as a newer build of an
  imported AppImage, import it under its own launcher ID instead of refusing it.
- Keep the update confirmation's "In Use Right Now" notice current: programs are
  re-checked when it opens and while it stays open, so quitting an app clears it.
