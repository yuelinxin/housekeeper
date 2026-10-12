# Changelog

## 0.3.0 — 2026-10-12

- Add an Install Apps page to the sidebar, replacing the header's + button. One
  search covers Flatpak, native packages and Snap, groups the sources that offer
  the same app, and offers the preferred source first (Preferences → Installing
  Apps). Snap apps open in the Snap Store.
- Mark search results that are already installed. Their source reads
  "(Installed)" and the button opens the installed app's details instead of
  installing it again; other sources of the same app can still be installed. Copies
  of an installed Flatpak on other remotes are no longer shown as installed.
- Enter a website address in the search to add a Chrome or Chromium web app, or
  open or drop a downloaded AppImage, RPM, DEB, `.flatpakref` or `.flatpak` bundle
  to review its details, origin and trust before installing. A `.flatpakrepo` file
  opens the Software Sources import in Preferences.
- Show sharper app icons on install pages, using the largest AppStream and bundle
  icons available.
- Keep working while apps install, update or uninstall: progress no longer opens a
  blocking window. The page an operation started from shows a spinner with its
  current step and Cancel, and the new Activity button at the top left of the
  sidebar lists running work with progress and Cancel, followed by this session's
  recent results.
- Announce successful and cancelled operations with a toast naming the app, with
  Details for the full message; partial and failed results still open a dialog.
- Simplify the Updates page. Without updates it shows only the status, when updates
  were last checked and a Check for Updates button; with updates, Check Again sits
  beside the count. Checks show a spinner in place of the status icon.
- Move app list search to the list's header, and use the same size for the large
  status icons across pages.
