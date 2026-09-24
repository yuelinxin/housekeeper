"""Installation inputs, desktop integration and exact package targets; no host changes."""

from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from appimage_fixture import image_bytes
from gi.repository import Gio, GLib

from housekeeper.appearance import can_reset_icon, save_icon
from housekeeper.discovery import scan_entries
from housekeeper.identity import classify
from housekeeper.installations import (
    InstallCandidate,
    Installer,
    InstallRequest,
    normalize_url,
    ranked_matches,
)
from housekeeper.models import Action, ManagementError, OperationCancelled, Outcome, Source
from housekeeper.providers import install as backend
from housekeeper.services import collect


@pytest.fixture(autouse=True)
def forget_remote_refs():
    backend.forget_remote_refs()
    yield
    backend.forget_remote_refs()


@pytest.fixture
def local_install(tmp_path, monkeypatch):
    monkeypatch.setattr("housekeeper.installations.website_icon", lambda *_: "web-browser")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    browser_dir = tmp_path / "browsers"
    browser_dir.mkdir()
    for name in ("google-chrome-stable", "chromium"):
        browser = browser_dir / name
        browser.write_text("#!/bin/sh\nexit 0\n")
        browser.chmod(0o755)
    monkeypatch.setattr(
        "housekeeper.installations.shutil",
        # Keep the browser fixture local: ownership checks still need the real
        # shutil.which to find dpkg-query on Debian and Ubuntu.
        NS(which=lambda name: str(browser_dir / name) if (browser_dir / name).exists() else None),
    )
    return tmp_path, Installer()


def test_web_url_default_name_and_inventory_classification(local_install):
    root, installer = local_install
    result = installer.install(InstallRequest("web", "example.org/notes"), lambda *_: None)
    assert result.outcome == Outcome.SUCCESS
    entries, warnings = scan_entries([root / "data/applications"])
    assert len(entries) == 1 and not warnings
    assert entries[0].name == "example.org"
    assert entries[0].housekeeper_created
    assert entries[0].argv == (
        str(root / "browsers/google-chrome-stable"),
        "--profile-directory=Default",
        "--app=https://example.org/notes",
    )
    assert classify(entries[0]).source == Source.WEB
    apps, *_ = collect(roots=[root / "data/applications"], indexes=[])
    assert len(apps) == 1 and apps[0].action == Action.UNINSTALL
    assert apps[0].provider == "web-launcher" and apps[0].scope == "User"
    assert apps[0].location == result.completed[0]
    info = Gio.DesktopAppInfo.new_from_filename(result.completed[0])
    assert info is not None and info.get_name() == "example.org"
    assert info.get_startup_wm_class() == "example.org__notes"
    assert Path(result.completed[0]).name == "chrome-example.org__notes-Default.desktop"
    original = Path(result.completed[0]).read_bytes()
    with pytest.raises(ManagementError, match="already"):
        installer.install(InstallRequest("web", "https://example.org/notes"), lambda *_: None)
    assert Path(result.completed[0]).read_bytes() == original


def test_launcher_is_written_as_utf8_whatever_the_session_locale_is(local_install):
    _root, installer = local_install
    result = installer.install(InstallRequest("web", "example.org", "Café Notes"), lambda *_: None)
    data = Path(result.completed[0]).read_bytes()
    assert "Name=Café Notes".encode() in data
    info = Gio.DesktopAppInfo.new_from_filename(result.completed[0])
    assert info is not None and info.get_name() == "Café Notes"


def test_web_exec_percent_and_metacharacters_stay_in_one_argument(local_install):
    root, installer = local_install
    url = 'https://example.org/a%20b?q="hello"&x=$HOME&z=`id`'
    result = installer.install(
        InstallRequest("web", url, "My Website", "chromium"), lambda *_: None
    )
    keyfile = GLib.KeyFile()
    keyfile.load_from_file(result.completed[0], GLib.KeyFileFlags.NONE)
    command = keyfile.get_string("Desktop Entry", "Exec")
    parsed = GLib.shell_parse_argv(command)[1]
    assert len(parsed) == 3
    assert parsed[0] == str(root / "browsers/chromium")
    assert parsed[1] == "--profile-directory=Default"
    assert parsed[2].replace("%%", "%") == "--app=" + url
    assert Gio.DesktopAppInfo.new_from_filename(result.completed[0]) is not None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///tmp/test",
        "javascript:alert(1)",
        "https://",
        "https://example.org:bad",
        "https://user:password@example.org",
        "https://example.org/\nExec=bad",
        "https://example.org\\evil",
        "ftp://example.org",
    ],
)
def test_invalid_urls(url):
    from housekeeper.website_icons import _http_url

    with pytest.raises(ManagementError):
        normalize_url(url)
    # Fetched icon and redirect URLs share the same rules as typed launcher URLs.
    if "://" in url:
        with pytest.raises(ValueError):
            _http_url(url)


def test_a_scheme_inside_the_path_or_query_does_not_replace_the_missing_one():
    assert (
        normalize_url("example.org/out?u=https://docs.example.org")
        == "https://example.org/out?u=https://docs.example.org"
    )
    assert normalize_url("HTTP://example.org") == "http://example.org/"


def test_a_query_with_spaces_is_not_reported_as_too_short():
    with pytest.raises(ManagementError, match="at least two characters"):
        Installer().search("rpm", "f")
    with pytest.raises(ManagementError, match="letters, numbers"):
        Installer().search("rpm", "gnome; maps")


def test_missing_chrome_does_not_silently_use_another_browser(local_install, monkeypatch):
    root, installer = local_install
    monkeypatch.setattr("housekeeper.installations.shutil.which", lambda _name: None)
    with pytest.raises(ManagementError, match="browser is not installed"):
        installer.install(InstallRequest("web", "example.org"), lambda *_: None)
    assert not (root / "data/applications").exists()


def test_appimage_copy_permissions_original_and_inventory(local_install):
    root, installer = local_install
    image = root / "Notes_x86_64.AppImage"
    image.write_bytes(image_bytes())
    image.chmod(0o600)
    result = installer.install(InstallRequest("appimage", str(image)), lambda *_: None)
    assert result.outcome == Outcome.SUCCESS
    copies = list((root / "AppImages").glob("*.AppImage"))
    assert len(copies) == 1 and copies[0].read_bytes() == image.read_bytes()
    assert copies[0].stat().st_mode & 0o777 == 0o755
    assert image.stat().st_mode & 0o777 == 0o600
    entries, _ = scan_entries([root / "data/applications"])
    assert entries[0].name == "Notes"
    assert entries[0].icon == "application-x-executable"
    assert classify(entries[0]).source == Source.APPIMAGE
    assert entries[0].argv == (str(copies[0]),)
    with pytest.raises(ManagementError, match="already"):
        installer.install(InstallRequest("appimage", str(image)), lambda *_: None)
    assert copies[0].exists()


@pytest.mark.parametrize("source", ["web", "appimage"])
def test_custom_icon_is_durable_preserves_duplicates_and_can_reset(local_install, source):
    root, installer = local_install
    image = root / "Notes.AppImage"
    image.write_bytes(image_bytes())
    picture = root / "chosen-icon.svg"
    picture.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">'
        '<rect width="800" height="600" fill="red"/></svg>'
    )
    request = InstallRequest(
        source, "example.org" if source == "web" else str(image), icon=str(picture)
    )
    result = installer.install(request, lambda *_: None)
    launcher = Path(result.completed[0])
    entry = scan_entries([launcher.parent])[0][0]
    icon = Path(entry.icon)
    assert icon.parent == root / "data/housekeeper/icons"
    contents = icon.read_bytes()
    assert contents.startswith(b"\x89PNG")
    assert int.from_bytes(contents[16:20], "big") == 512
    assert int.from_bytes(contents[20:24], "big") == 384
    assert can_reset_icon(entry)
    before = launcher.read_bytes()
    picture.write_text(picture.read_text().replace("red", "blue"))
    with pytest.raises(ManagementError, match="already"):
        installer.install(request, lambda *_: None)
    assert launcher.read_bytes() == before and icon.read_bytes() == contents
    # The refused install's normalized blue icon is not left behind.
    assert list(icon.parent.iterdir()) == [icon]
    picture.unlink()
    assert icon.read_bytes() == contents
    save_icon(entry)
    restored = scan_entries([launcher.parent])[0][0]
    assert restored.icon == ("web-browser" if source == "web" else "application-x-executable")
    assert restored.argv == entry.argv and not can_reset_icon(restored)


def test_website_icon_is_default_and_custom_icon_reset_restores_it(local_install, monkeypatch):
    root, installer = local_install
    website = root / "website.png"
    website.write_bytes(b"stored website icon")
    lookup = Mock(return_value=str(website))
    monkeypatch.setattr("housekeeper.installations.website_icon", lookup)
    result = installer.install(InstallRequest("web", "example.org"), lambda *_: None)
    entry = scan_entries([Path(result.completed[0]).parent])[0][0]
    assert entry.icon == str(website) and not can_reset_icon(entry)
    assert lookup.call_args.args[0] == "https://example.org/"
    custom = root / "custom.svg"
    custom.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"/>')
    result = installer.install(
        InstallRequest("web", "example.org/custom", icon=str(custom)), lambda *_: None
    )
    entry = next(
        e
        for e in scan_entries([Path(result.completed[0]).parent])[0]
        if e.path == Path(result.completed[0])
    )
    assert entry.icon != str(website) and can_reset_icon(entry)
    save_icon(entry)
    restored = next(e for e in scan_entries([entry.path.parent])[0] if e.path == entry.path)
    assert restored.icon == str(website) and not can_reset_icon(restored)


@pytest.mark.parametrize("source", ["web", "appimage"])
@pytest.mark.parametrize("kind", ["invalid", "oversized"])
def test_invalid_custom_icon_does_not_publish_app(local_install, source, kind):
    root, installer = local_install
    image = root / "Notes.AppImage"
    image.write_bytes(image_bytes())
    picture = root / "bad.png"
    picture.write_bytes(b"not an image")
    if kind == "oversized":
        with picture.open("r+b") as stream:
            stream.truncate(10 * 1024 * 1024 + 1)
    request = InstallRequest(
        source, "example.org" if source == "web" else str(image), icon=str(picture)
    )
    with pytest.raises((ManagementError, GLib.Error)):
        installer.install(request, lambda *_: None)
    assert not list((root / "data/applications").glob("*"))
    assert not list((root / "AppImages").glob("*"))
    assert not list((root / "data/housekeeper/icons").glob("*"))
    assert image.exists() and picture.exists()


@pytest.mark.parametrize("kind", ["invalid", "symlink", "directory", "cancel", "launcher-failure"])
def test_appimage_failures_leave_no_copy_or_launcher(local_install, monkeypatch, kind):
    root, installer = local_install
    image = root / "Example.AppImage"
    image.write_bytes(image_bytes() if kind != "invalid" else b"not an AppImage")
    selected = image
    if kind == "symlink":
        selected = root / "link.AppImage"
        selected.symlink_to(image)
    elif kind == "directory":
        selected = root
    elif kind == "cancel":
        installer.request_cancel()
    elif kind == "launcher-failure":
        monkeypatch.setattr(
            "housekeeper.installations.create_launcher", Mock(side_effect=OSError("Disk full"))
        )
    with pytest.raises((ManagementError, OperationCancelled, OSError)):
        installer.install(InstallRequest("appimage", str(selected)), lambda *_: None)
    assert image.exists()
    assert not list((root / "AppImages").glob("*"))
    assert not list((root / "data/applications").glob("*"))


def test_small_fuzzy_search_ranks_exact_prefix_substring_then_typos():
    candidates = [
        InstallCandidate("rpm", name, name)
        for name in ["firefox-l10n", "libfirefox", "firefox", "firefix", "unrelated"]
    ]
    assert [p.name for p in ranked_matches("firefox", candidates)] == [
        "firefox",
        "firefox-l10n",
        "libfirefox",
        "firefix",
    ]
    flatpaks = [
        InstallCandidate("flatpak", name, name)
        for name in ["org.mozilla.firefox", "org.gnome.Maps", "org.example.FirefoxBeta"]
    ]
    assert ranked_matches("firefx", flatpaks)[0].name == "org.mozilla.firefox"
    assert len(ranked_matches("fire", candidates, limit=2)) == 2


def packages_result(packages=()):
    return NS(
        get_exit_code=lambda: 1,
        get_error_code=lambda: None,
        get_package_array=lambda: list(packages),
    )


def packagekit(monkeypatch, packages=(), *, installed=(), available=(), apps=None):
    pk = NS(
        FilterEnum=NS(NOT_INSTALLED=1, NEWEST=2, ARCH=3, GUI=4, INSTALLED=5),
        TransactionFlagEnum=NS(ONLY_TRUSTED=2),
        ExitEnum=NS(SUCCESS=1, CANCELLED=2),
    )
    result = packages_result(packages)

    def resolve(filters, names, *_args):
        pool = installed if filters & 1 << pk.FilterEnum.INSTALLED else available
        return packages_result([p for p in pool if p.get_name() in names])

    client = NS(
        search_names=Mock(return_value=result),
        resolve=Mock(side_effect=resolve),
        install_packages=Mock(return_value=result),
    )
    monkeypatch.setattr(backend, "package_client", lambda source: (client, pk))
    monkeypatch.setattr(backend, "appstream_apps", lambda *_args: apps)
    return client, pk, result


def rpm_package(name, summary="Summary", repo="updates"):
    return NS(
        get_name=lambda: name,
        get_id=lambda: f"{name};1;x86_64;{repo}",
        get_version=lambda: "1",
        get_arch=lambda: "x86_64",
        get_summary=lambda: summary,
        get_data=lambda: repo,
    )


def app_component(title, summary, icon=""):
    icons = [NS(get_width=lambda: 64, get_height=lambda: 64, get_filename=lambda: icon)]
    icons = icons if icon else []
    return NS(get_name=lambda: title, get_summary=lambda: summary, get_icons=lambda: icons)


def test_catalogue_apps_come_first_in_catalogue_order_without_installed_ones(monkeypatch, tmp_path):
    icon = tmp_path / "te.png"
    icon.write_bytes(b"png")
    apps = {
        "gnome-text-editor": app_component("Text Editor", "Edit text files", str(icon)),
        "kate": app_component("Kate", "Advanced text editor"),
        "gedit": app_component("gedit", "Text editor"),
        "unavailable": app_component("Gone", "Not in any enabled repository"),
    }
    editor, kate, gedit = (rpm_package(n) for n in ("gnome-text-editor", "kate", "gedit"))
    client, pk, _ = packagekit(
        monkeypatch,
        # dnf5 lists an installed build as available when a repository carries it too.
        available=[kate, gedit, editor],
        installed=[kate],
        apps=apps,
    )
    candidates = Installer().search("rpm", "text")
    assert [c.name for c in candidates] == ["gnome-text-editor", "gedit"]
    assert [c.title for c in candidates] == ["Text Editor", "gedit"]
    assert candidates[0].icon == str(icon) and candidates[1].icon == ""
    assert candidates[0].target == "gnome-text-editor;1;x86_64;updates"
    assert candidates[0].description == "Edit text files · gnome-text-editor · 1 · x86_64 · updates"
    client.search_names.assert_not_called()
    filters = [call.args[0] for call in client.resolve.call_args_list]
    assert filters[0] & 1 << pk.FilterEnum.NOT_INSTALLED
    assert filters[1] & 1 << pk.FilterEnum.INSTALLED


def test_a_named_catalogue_app_ranks_before_a_keyword_match(monkeypatch):
    apps = {
        "ausweisapp": app_component("AusweisApp", "Office identity card"),
        "libreoffice-draw": app_component("LibreOffice Draw", "Create diagrams"),
        "office-runner": app_component("Office Runner", "Prevent suspend"),
    }
    packagekit(monkeypatch, available=[rpm_package(name) for name in apps], apps=apps)
    names = [c.name for c in Installer().search("rpm", "office")]
    assert names == ["libreoffice-draw", "office-runner", "ausweisapp"]


@pytest.mark.parametrize("apps", [None, {}, {"kate": app_component("Kate", "Editor")}])
def test_desktop_package_fallback_without_an_installable_catalogue_match(monkeypatch, apps):
    chrome, firefox = rpm_package("google-chrome-stable", repo="google"), rpm_package("firefox")
    client, pk, found = packagekit(
        monkeypatch, [chrome, firefox], installed=[firefox, rpm_package("kate")], apps=apps
    )
    candidates = Installer().search("rpm", "chrome")
    assert [(c.name, c.title) for c in candidates] == [("google-chrome-stable", "")]
    # Only packages that provide a desktop application: no plugins, libraries or -devel.
    assert client.search_names.call_args.args[0] & 1 << pk.FilterEnum.GUI


def test_package_search_typo_fallback_and_install_exact_selected_id(monkeypatch):
    client, pk, found = packagekit(monkeypatch, [rpm_package("firefox", "Browser")])
    empty = packages_result()
    client.search_names.side_effect = [empty, found]
    installer = Installer()
    candidates = installer.search("rpm", "firefx")
    assert len(candidates) == 1
    assert [call.args[1] for call in client.search_names.call_args_list] == [["firefx"], ["fir"]]
    # Short queries never fall back to a two-character substring of the whole catalogue.
    client.search_names.side_effect = [empty, empty]
    installer.search("rpm", "gimq")
    assert client.search_names.call_args.args[1] == ["gim"]
    assert all(callable(call.args[3]) for call in client.search_names.call_args_list)
    result = installer.install(InstallRequest("rpm", candidate=candidates[0]), lambda *_: None)
    assert result.outcome == Outcome.SUCCESS
    flags, targets, *_ = client.install_packages.call_args.args
    assert flags == 1 << pk.TransactionFlagEnum.ONLY_TRUSTED
    assert targets == ["firefox;1;x86_64;updates"]


def test_install_requires_selection_and_matching_source(monkeypatch):
    client, _, _ = packagekit(monkeypatch)
    for request in (
        InstallRequest("rpm", "firefox"),
        InstallRequest("deb", candidate=InstallCandidate("rpm", "firefox", "id")),
    ):
        with pytest.raises(ManagementError):
            Installer().install(request, lambda *_: None)
    client.install_packages.assert_not_called()


def test_package_manager_failure_is_not_success(monkeypatch):
    client, _, result = packagekit(monkeypatch)
    result.get_error_code = lambda: NS(get_details=lambda: "Authorization denied")
    with pytest.raises(ManagementError, match="Authorization denied"):
        Installer().install(
            InstallRequest(
                "deb", candidate=InstallCandidate("deb", "firefox", "firefox;1;amd64;repo")
            ),
            lambda *_: None,
        )


def test_native_source_mismatch_never_contacts_packagekit(monkeypatch):
    monkeypatch.setattr(backend, "native_package_source", lambda: ("deb", "DEB"))
    with pytest.raises(ManagementError, match="native package type"):
        backend.package_client("rpm")


def test_flatpak_search_keeps_scope_branch_and_remote(monkeypatch):
    def ref(name, kind=1, arch="x86_64", branch="stable"):
        return NS(
            get_name=lambda: name,
            get_kind=lambda: kind,
            get_arch=lambda: arch,
            get_branch=lambda: branch,
            format_ref=lambda: f"app/{name}/{arch}/{branch}",
        )

    remote = NS(
        get_disabled=lambda: False,
        get_noenumerate=lambda: False,
        get_name=lambda: "flathub",
        get_appstream_dir=lambda _arch: None,
    )
    refs = [
        ref("org.mozilla.firefox"),
        ref("org.mozilla.firefox", branch="beta"),
        ref("org.mozilla.firefox", arch="aarch64"),
        ref("org.mozilla.firefox.Locale", kind=2),
        ref("org.gnome.Maps"),
    ]
    installations = [
        NS(
            get_is_user=lambda: True,
            get_path=lambda p=path: Gio.File.new_for_path(p),
            list_remotes=lambda _: [remote],
            list_installed_refs=lambda _: [ref("org.gnome.Maps")],
            list_remote_refs_sync=lambda *_: refs,
        )
        for path in ("/user/flatpak", "/system/flatpak")
    ]
    monkeypatch.setattr(
        backend, "load_flatpak", lambda: NS(RefKind=NS(APP=1), get_default_arch=lambda: "x86_64")
    )
    monkeypatch.setattr(backend, "configured_installations", lambda _: (installations, []))
    candidates = Installer().search("flatpak", "firefx")
    assert len(candidates) == 4
    assert {c.context for c in candidates} == {"/user/flatpak", "/system/flatpak"}
    assert {c.remote for c in candidates} == {"flathub"}
    assert all(c.target.startswith("app/org.mozilla.firefox/x86_64/") for c in candidates)


def flatpak_installation(refs, monkeypatch, warnings=(), path="/user/flatpak"):
    remote = NS(
        get_disabled=lambda: False,
        get_noenumerate=lambda: False,
        get_name=lambda: "flathub",
        get_appstream_dir=lambda _arch: None,
    )
    installation = NS(
        get_is_user=lambda: True,
        get_path=lambda: Gio.File.new_for_path(path),
        list_remotes=lambda _: [remote],
        list_installed_refs=lambda _: [],
        list_remote_refs_sync=Mock(return_value=refs),
    )
    monkeypatch.setattr(
        backend, "load_flatpak", lambda: NS(RefKind=NS(APP=1), get_default_arch=lambda: "x86_64")
    )
    monkeypatch.setattr(
        backend, "configured_installations", lambda _: ([installation], list(warnings))
    )
    return installation


def flatpak_ref(name, kind=1, arch="x86_64", branch="stable"):
    return NS(
        get_name=lambda: name,
        get_kind=lambda: kind,
        get_arch=lambda: arch,
        get_branch=lambda: branch,
        format_ref=lambda: f"app/{name}/{arch}/{branch}",
    )


def test_an_unrelated_source_warning_does_not_mask_an_empty_result(monkeypatch):
    flatpak_installation(
        [flatpak_ref("org.gnome.Maps")],
        monkeypatch,
        warnings=["Flatpak system installation discovery failed: permission denied"],
    )
    assert Installer().search("flatpak", "zzzq") == ()


def test_a_search_that_could_read_nothing_reports_why(monkeypatch):
    installation = flatpak_installation([], monkeypatch)
    installation.list_remote_refs_sync.side_effect = GLib.Error("the remote summary is unreadable")
    with pytest.raises(ManagementError, match="unreadable"):
        Installer().search("flatpak", "firefox")


def test_no_configured_source_is_reported_separately_from_a_failure(monkeypatch):
    installation = flatpak_installation([], monkeypatch)
    installation.list_remotes = lambda _: []
    with pytest.raises(ManagementError, match="No Flatpak sources are configured"):
        Installer().search("flatpak", "firefox")


def test_remote_catalogue_is_enumerated_once_across_keystrokes(monkeypatch):
    installation = flatpak_installation([flatpak_ref("org.mozilla.firefox")], monkeypatch)
    for query in ("fir", "fire", "firefox"):
        assert Installer().search("flatpak", query)[0].name == "org.mozilla.firefox"
    installation.list_remote_refs_sync.assert_called_once()
    backend.forget_remote_refs()
    assert Installer().search("flatpak", "firefox")
    assert installation.list_remote_refs_sync.call_count == 2


def test_an_installed_application_is_dropped_from_a_cached_catalogue(monkeypatch):
    installation = flatpak_installation([flatpak_ref("org.mozilla.firefox")], monkeypatch)
    assert Installer().search("flatpak", "firefox")
    installation.list_installed_refs = lambda _: [flatpak_ref("org.mozilla.firefox")]
    assert Installer().search("flatpak", "firefox") == ()
    installation.list_remote_refs_sync.assert_called_once()


@pytest.mark.parametrize("outcome", ["success", "failed", "partial", "cancelled"])
def test_flatpak_install_uses_selected_remote_ref_and_context(monkeypatch, outcome):
    callbacks = {}
    tx = Mock()
    tx.connect.side_effect = lambda signal, callback: callbacks.setdefault(signal, callback)
    remotes = [
        NS(get_name=lambda name=name: name, get_disabled=lambda off=off: off)
        for name, off in (("flathub", False), ("beta", False), ("off", True))
    ]
    installation = NS(
        get_path=lambda: Gio.File.new_for_path("/chosen/user"), list_remotes=lambda _: remotes
    )
    fp = NS(Transaction=NS(new_for_installation=Mock(return_value=tx)))
    monkeypatch.setattr(backend, "load_flatpak", lambda: fp)
    monkeypatch.setattr(backend, "configured_installations", lambda _: ([installation], []))
    installer = Installer()
    candidate = InstallCandidate(
        "flatpak",
        "org.example.App",
        "app/org.example.App/x86_64/stable",
        context="/chosen/user",
        remote="flathub",
    )

    def run(cancel):
        assert callbacks["ready"](tx)
        assert not callbacks["add-new-remote"](tx, "unconfirmed", "https://example.org")
        choose = callbacks["choose-remote-for-ref"]
        assert choose(tx, candidate.target, "runtime", ["flathub"]) == 0
        # A runtime offered by several remotes comes from the selected one, then the
        # first enabled remote of this installation; unknown remotes are never used.
        assert choose(tx, candidate.target, "runtime", ["other", "flathub"]) == 1
        assert choose(tx, candidate.target, "runtime", ["flathub", "other"]) == 0
        assert choose(tx, candidate.target, "runtime", ["off", "beta"]) == 1
        assert choose(tx, candidate.target, "runtime", ["off", "unknown"]) == -1
        if outcome in {"success", "partial"}:
            callbacks["operation-done"](
                tx,
                NS(
                    get_ref=lambda: (
                        candidate.target
                        if outcome == "success"
                        else "runtime/dependency/x86_64/stable"
                    )
                ),
                "commit",
                None,
            )
        if outcome == "cancelled":
            cancel.cancel()
        if outcome != "success":
            callbacks["operation-error"](tx, None, RuntimeError("Stopped"), None)
        return outcome == "success"

    tx.run.side_effect = run
    result = installer.install(InstallRequest("flatpak", candidate=candidate), lambda *_: None)
    assert (
        result.outcome
        == {
            "success": Outcome.SUCCESS,
            "failed": Outcome.FAILED,
            "partial": Outcome.PARTIAL,
            "cancelled": Outcome.CANCELLED,
        }[outcome]
    )
    tx.add_install.assert_called_once_with("flathub", candidate.target, None)
    assert fp.Transaction.new_for_installation.call_args.args[0] is installation


def snapd(monkeypatch, tmp_path, present=True):
    socket = tmp_path / "snapd.socket"
    if present:
        socket.touch()
    monkeypatch.setattr("housekeeper.providers.snap.SNAP_SOCKET", socket)


def test_snap_search_without_snapd_reports_the_missing_service(monkeypatch, tmp_path):
    connection = Mock(side_effect=AssertionError("snapd must not be contacted"))
    snapd(monkeypatch, tmp_path, present=False)
    monkeypatch.setattr(
        "housekeeper.providers.snap.SnapConnection", lambda *_args, **_kwargs: connection
    )
    with pytest.raises(ManagementError, match="not available"):
        Installer().search("snap", "fire")
    connection.request.assert_not_called()


def test_snap_search_reports_a_refused_socket_without_raw_errno(monkeypatch, tmp_path):
    connection = Mock()
    connection.request.side_effect = ConnectionRefusedError(111, "Connection refused")
    snapd(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "housekeeper.providers.snap.SnapConnection", lambda *_args, **_kwargs: connection
    )
    with pytest.raises(ManagementError) as error:
        Installer().search("snap", "fire")
    assert "Errno" not in str(error.value) and "unavailable" in str(error.value)
    connection.close.assert_called_once()


def test_snap_search_keeps_store_package_name(monkeypatch, tmp_path):
    import json

    snapd(monkeypatch, tmp_path)
    connection = Mock()
    connection.getresponse.return_value = NS(
        status=200,
        read=lambda _: json.dumps(
            {
                "type": "sync",
                "result": [{"name": "firefox", "summary": "Browser"}, {"name": "other"}],
            }
        ).encode(),
    )
    monkeypatch.setattr(
        "housekeeper.providers.snap.SnapConnection", lambda *_args, **_kwargs: connection
    )
    result = Installer().search("snap", "fire")
    assert len(result) == 1 and result[0].name == "firefox"
    connection.request.assert_called_once_with("GET", "/v2/find?q=fire")
    connection.close.assert_called_once()


@pytest.mark.parametrize("kind", ["stale-launcher", "cancel-after-icon"])
def test_a_failed_install_leaves_no_new_icon(local_install, monkeypatch, kind):
    root, installer = local_install
    icons = root / "data/housekeeper/icons"
    picture = root / "chosen-icon.svg"
    picture.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"/>')
    if kind == "stale-launcher":
        # The copied AppImage was deleted by hand, but its launcher is still installed.
        image = root / "Notes.AppImage"
        image.write_bytes(image_bytes())
        result = installer.install(InstallRequest("appimage", str(image)), lambda *_: None)
        next((root / "AppImages").iterdir()).unlink()
        request = InstallRequest("appimage", str(image), icon=str(picture))
        error = ManagementError
    else:
        website = root / "website.png"
        website.write_bytes((root / "chosen-icon.svg").read_bytes())
        from housekeeper.appearance import store_icon_image

        def fetched(_url, _check, created):
            icon = store_icon_image(website, created)
            installer.request_cancel()  # Cancel arrives while the icon is being fetched.
            return str(icon)

        monkeypatch.setattr("housekeeper.installations.website_icon", fetched)
        request = InstallRequest("web", "example.org")
        error = OperationCancelled
    with pytest.raises(error):
        installer.install(request, lambda *_: None)
    assert not icons.exists() or not list(icons.iterdir())
    if kind == "stale-launcher":
        assert Path(result.completed[0]).exists()


def test_appstream_catalogue_yields_only_packaged_desktop_apps(tmp_path, monkeypatch):
    try:
        import gi

        gi.require_version("AppStream", "1.0")
        from gi.repository import AppStream as As
    except (ImportError, ValueError):
        pytest.skip("AppStream introspection is unavailable")
    icon = tmp_path / "editor.png"
    icon.write_bytes(b"png")
    (tmp_path / "catalog").mkdir()
    (tmp_path / "catalog/fixture.xml").write_text(
        '<?xml version="1.0"?>\n<components version="1.0" origin="fixture">'
        '<component type="desktop-application"><id>org.example.Editor</id>'
        "<name>Example Editor</name><summary>Edit text files</summary>"
        f'<pkgname>example-editor</pkgname><icon type="local">{icon}</icon></component>'
        '<component type="desktop-application"><id>org.example.Bundled</id>'
        "<name>Bundled Editor</name><summary>Edit text from a bundle</summary>"
        '<bundle type="flatpak">app/org.example.Bundled/x86_64/stable</bundle></component>'
        '<component type="console-application"><id>org.example.edit-cli</id>'
        "<name>edit-cli</name><summary>Edit text in a terminal</summary>"
        "<pkgname>edit-cli</pkgname></component>"
        '<component type="addon"><id>org.example.Editor.Spell</id><extends>org.example.Editor'
        "</extends><name>Editor Spelling</name><summary>Edit text with spelling</summary>"
        "<pkgname>example-editor-spell</pkgname></component>"
        "</components>"
    )
    pool = As.Pool()
    pool.set_flags(As.PoolFlags.NONE)
    pool.add_extra_data_location(str(tmp_path / "catalog"), As.FormatStyle.CATALOG)
    pool.load(None)
    monkeypatch.setattr(backend, "_appstream", [pool])
    apps = backend.appstream_apps(Installer(), "edit")
    assert list(apps) == ["example-editor"]
    assert apps["example-editor"].get_name() == "Example Editor"
    assert backend.appstream_icon(apps["example-editor"]) == str(icon)
    backend.forget_remote_refs()
    assert backend._appstream == []


def test_flatpak_search_uses_each_remotes_appstream_catalogue(tmp_path, monkeypatch):
    import gzip
    import os

    if backend.load_appstream() is None:
        pytest.skip("AppStream introspection is unavailable")
    directory = tmp_path / "appstream/flathub/x86_64/active"
    (directory / "icons/64x64").mkdir(parents=True)
    (directory / "icons/64x64/com.obsproject.Studio.png").write_bytes(b"png")

    def app(ident, name, summary, kind="desktop-application", keyword=""):
        ref = f"app/{ident}/x86_64/stable"
        keywords = f"<keywords><keyword>{keyword}</keyword></keywords>" if keyword else ""
        return (
            f'<component type="{kind}"><id>{ident}</id><name>{name}</name>{keywords}'
            f'<summary>{summary}</summary><bundle type="flatpak">{ref}</bundle>'
            f'<icon type="cached" width="64" height="64">{ident}.png</icon></component>'
        )

    def publish(*components):
        xml = '<?xml version="1.0"?><components version="1.0" origin="flathub">'
        (directory / "appstream.xml.gz").write_bytes(
            gzip.compress((xml + "".join(components) + "</components>").encode())
        )

    components = [
        app("org.example.Recorder", "Recorder", "Record the screen", keyword="obs"),
        app("com.obsproject.Studio", "OBS Studio", "Live stream and record videos"),
        app("com.obsproject.Studio.Plugin.Obs", "Obs Plugin", "An obs plugin", kind="addon"),
        app("org.example.Unlisted", "Unlisted Obs", "Not offered by this remote"),
        app("org.example.Installed", "Installed Obs", "Already installed"),
    ]
    publish(*components)
    installation = flatpak_installation(
        [
            flatpak_ref(name)
            for name in ("org.example.Recorder", "com.obsproject.Studio", "org.example.Installed")
        ],
        monkeypatch,
    )
    installation.list_installed_refs = lambda _: [flatpak_ref("org.example.Installed")]
    remote = installation.list_remotes(None)[0]
    remote.get_appstream_dir = lambda _arch: Gio.File.new_for_path(str(directory))
    found = Installer().search("flatpak", "obs")
    assert [c.name for c in found] == ["com.obsproject.Studio", "org.example.Recorder"]
    studio = found[0]
    assert studio.title == "OBS Studio" and studio.remote == "flathub"
    assert studio.icon == str(directory / "icons/64x64/com.obsproject.Studio.png")
    assert studio.description.startswith("Live stream and record videos · com.obsproject.Studio")
    assert found[1].icon == ""  # A cached icon that is missing on disk is not shown.
    # The parsed catalogue is reused until Flatpak publishes a new one.
    pool = backend.remote_catalogue(installation, remote)[0]
    assert backend.remote_catalogue(installation, remote)[0] is pool
    publish(*components[1:])
    stat = (directory / "appstream.xml.gz").stat()
    os.utime(directory / "appstream.xml.gz", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert backend.remote_catalogue(installation, remote)[0] is not pool
    assert [c.name for c in Installer().search("flatpak", "obs")] == ["com.obsproject.Studio"]


def test_a_phrase_reaches_appstream_whole_and_name_search_by_its_longest_word(monkeypatch):
    seen = []
    apps = {"openshot": app_component("OpenShot Video Editor", "Create videos")}
    client, pk, _ = packagekit(monkeypatch, available=[rpm_package("openshot")])
    monkeypatch.setattr(
        backend, "appstream_apps", lambda _worker, query: seen.append(query) or apps
    )
    assert [c.title for c in Installer().search("rpm", "  video \t editor ")] == [
        "OpenShot Video Editor"
    ]
    assert seen == ["video editor"]
    # Without a catalogue match, names are searched by the longest word only.
    monkeypatch.setattr(backend, "appstream_apps", lambda *_: {})
    client.search_names.return_value = packages_result([rpm_package("shotcut-video")])
    found = Installer().search("rpm", "video editors")
    assert client.search_names.call_args.args[1] == ["editors"]
    assert found == ()


def test_a_phrase_counts_as_named_only_when_every_word_is_in_the_name():
    assert not backend.named_first("text editor", "COSMIC Text Editor", "cosmic-edit")
    assert backend.named_first("text editor", "Kate", "kate")
    assert backend.named_first("video editor", "Video Player", "")
