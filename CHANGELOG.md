# Changelog

## 0.3.2 — 2026-10-13

- Keep the Updates page usable while apps update. Instead of a spinner covering the
  page, each app in the update shows a short progress bar at the end of its row, and
  apps waiting their turn show an empty one. Each app leaves the list, and the
  sidebar count goes down, as soon as its own update finishes. Cancel replaces the
  update buttons, and the footer says which update is running.
- Show smoother overall progress for update batches in Activity.
- Select an update by clicking anywhere on its row, and highlight selected updates
  with a light accent tint. Leave more room between the checkbox and the app icon,
  and match the footer's bottom margin to its side margins.
- Give Install Apps search results the same vertical spacing and 40px icons as
  Updates rows.
