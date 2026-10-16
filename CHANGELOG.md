# Changelog

## 0.3.3 — 2026-10-15

- Download system package updates in parallel. A new Parallel Downloads preference,
  under Updating Apps, sets how many RPM app updates download at once (1, 2 or 3;
  3 by default). Each app still installs and is checked on its own, one at a time,
  and 1 keeps the previous behavior. Flatpak apps still download one at a time.
- Keep the heading under the large status page icon the same size before and after
  a search or update on Install Apps and Updates.
- Show "Searching for Apps" under the Install Apps spinner while a search runs.
