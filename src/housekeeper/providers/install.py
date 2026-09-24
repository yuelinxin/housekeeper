"""PackageKit and Flatpak installation from explicitly selected search results."""

import time
from dataclasses import replace
from pathlib import Path

from gi.repository import Gio, GLib

from housekeeper.i18n import _
from housekeeper.installations import InstallCandidate, ranked_matches, search_term
from housekeeper.models import ManagementError, OperationCancelled, OperationResult, Outcome
from housekeeper.platforms import native_package_source
from housekeeper.providers.flatpak import configured_installations, load_flatpak

# Remote catalogues are large and change rarely; a debounced keystroke must not
# re-download every summary. Only the ranking is redone for each query.
REMOTE_REF_TTL = 120.0
_remote_refs: dict[tuple[str, str], tuple[float, tuple[InstallCandidate, ...]]] = {}
# The loaded AppStream pool; like the refs, it is used only on the search thread.
_appstream: list = []
# Each Flatpak remote's parsed AppStream catalogue, reused until Flatpak replaces it.
_flatpak_catalogues: dict[tuple[str, str], tuple] = {}


def forget_remote_refs():
    _remote_refs.clear()
    _appstream.clear()
    _flatpak_catalogues.clear()


def load_appstream():
    import gi

    try:
        gi.require_version("AppStream", "1.0")
        from gi.repository import AppStream as As
    except (ImportError, ValueError):
        return None
    return As


def search_snaps(worker, query):
    import json
    from urllib.parse import urlencode

    from housekeeper.providers.snap import SNAP_SOCKET, SnapConnection

    if not SNAP_SOCKET.exists():
        raise ManagementError(_("Snap is not available on this system."))
    connection = SnapConnection("localhost", timeout=5)
    try:
        try:
            connection.request("GET", "/v2/find?" + urlencode({"q": query}))
            response = connection.getresponse()
            payload = response.read(8 * 1024 * 1024 + 1)
        except OSError as error:
            raise ManagementError(_("The Snap Store search is unavailable.")) from error
        worker.check_cancelled()
        if response.status != 200 or len(payload) > 8 * 1024 * 1024:
            raise ManagementError(_("The Snap Store search is unavailable."))
        result = json.loads(payload)
        if (
            not isinstance(result, dict)
            or result.get("type") != "sync"
            or not isinstance(result.get("result"), list)
        ):
            raise ManagementError(_("The Snap Store returned an invalid search result."))
        # The store matches the whole phrase; its package names are ranked by one word.
        return ranked_matches(
            search_term(query),
            (
                InstallCandidate("snap", item["name"], item["name"], str(item.get("summary", "")))
                for item in result["result"]
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ),
        )
    finally:
        connection.close()


def package_client(source):
    from pathlib import Path

    import gi

    native, _title = native_package_source()
    if source != native:
        raise ManagementError(
            _("Choose this system's native package type to install system packages.")
        )
    if any(Path(p).exists() for p in ("/run/ostree-booted", "/run/bootc", "/sysroot/ostree")):
        raise ManagementError(
            _("Use your system's software manager to add packages on this immutable system.")
        )
    try:
        gi.require_version("PackageKitGlib", "1.0")
        from gi.repository import PackageKitGlib as Pk
    except (ImportError, ValueError):
        raise ManagementError(
            _("PackageKit is required to search for and install system packages.")
        ) from None
    client = Pk.Client()
    client.set_interactive(True)
    client.set_cache_age(3600)
    return client, Pk


def checked_packages(result, pk):
    if result.get_exit_code() == pk.ExitEnum.CANCELLED:
        raise OperationCancelled(_("The package operation was cancelled."))
    error = result.get_error_code()
    if error:
        raise ManagementError(
            error.get_details() or _("The package manager rejected the operation.")
        )
    if result.get_exit_code() != pk.ExitEnum.SUCCESS:
        raise ManagementError(_("The package manager did not report success."))
    return result.get_package_array()


def appstream_apps(worker, query, limit=24):
    """Desktop apps in the distribution's AppStream catalogue, best match first.

    Maps each package name to its component, or returns None without a catalogue.
    """
    As = load_appstream()
    if As is None:
        return None
    if not _appstream:
        pool = As.Pool()
        # Only the distribution catalogue names packages; Flatpak is searched separately.
        pool.set_flags(As.PoolFlags.LOAD_OS_CATALOG)
        try:
            pool.load(worker.cancel)
        except GLib.Error:
            worker.check_cancelled()
            return None
        _appstream.append(pool)
    apps = {}
    for component in _appstream[0].search(query).as_array():
        name = component.get_pkgname()
        if component.get_kind() == As.ComponentKind.DESKTOP_APP and name and name not in apps:
            apps[name] = component
            if len(apps) == limit:
                break
    return apps


def appstream_icon(component, directory=None, size=64):
    """The existing icon file closest to `size`; cached names resolve in `directory`."""
    files = []
    for icon in component.get_icons():
        name = icon.get_filename() or ""
        if name and not Path(name).is_absolute():
            # A remote's catalogue names cached icons relative to its icons directory.
            if directory is None or "/" in name or name.startswith("."):
                continue
            name = str(directory / "icons" / f"{icon.get_width()}x{icon.get_height()}" / name)
        if name and Path(name).is_file():
            files.append((abs(icon.get_width() - size), name))
    return min(files)[1] if files else ""


def named_first(query, title, name):
    """AppStream weighs keyword and name matches alike; a named app sorts first."""
    words = query.casefold().split()
    return not any(
        value and all(word in value.casefold() for word in words) for value in (title, name)
    )


def search_packages(worker, source, query):
    client, pk = package_client(source)
    available = sum(
        1 << int(value)
        for value in (pk.FilterEnum.NOT_INSTALLED, pk.FilterEnum.NEWEST, pk.FilterEnum.ARCH)
    )

    def call(method, filters, terms):
        worker.check_cancelled()
        return checked_packages(
            method(filters, terms, worker.cancel, lambda *_args: None, None), pk
        )

    def installable(packages):
        # dnf5 also reports an installed package as not installed when a repository
        # carries the same build, so exclude installed names explicitly.
        names = sorted({p.get_name() for p in packages})
        if not names:
            return []
        installed = sum(1 << int(v) for v in (pk.FilterEnum.INSTALLED, pk.FilterEnum.ARCH))
        present = {p.get_name() for p in call(client.resolve, installed, names)}
        return [p for p in packages if p.get_name() not in present]

    def candidate(p, app=None):
        details = (p.get_version(), p.get_arch(), p.get_data())
        if app is None:
            description = (*details, p.get_summary())
        else:
            description = (app.get_summary(), p.get_name(), *details)
        return InstallCandidate(
            source,
            p.get_name(),
            p.get_id(),
            " · ".join(v for v in description if v),
            remote=p.get_data() or "",
            title=app.get_name() if app is not None else "",
            icon=appstream_icon(app) if app is not None else "",
        )

    # The catalogue lists real desktop apps, so plugins, libraries and -devel
    # packages never appear, and names, summaries and keywords all match.
    apps = appstream_apps(worker, query)
    if apps:
        packages = {p.get_name(): p for p in installable(call(client.resolve, available, [*apps]))}
        ranked = sorted(
            (item for item in apps.items() if item[0] in packages),
            key=lambda item: named_first(query, item[1].get_name(), item[0]),
        )
        found = [candidate(packages[name], app) for name, app in ranked]
        if found:
            return tuple(found[:12])
    # Packages without catalogue metadata, as in many third-party repositories: only
    # those providing a desktop application (application() RPM provides). Names hold
    # no phrases, so only the longest word is searched.
    term = search_term(query)
    gui = available | 1 << int(pk.FilterEnum.GUI)
    packages = call(client.search_names, gui, [term])
    # One bounded prefix fallback gives modest typo tolerance without enumerating
    # the host's entire package catalogue or generating many backend requests.
    # PackageKit matches names by substring, so two characters match much of the catalogue.
    if not packages and len(term) >= 4:
        packages = call(client.search_names, gui, [term[: max(3, len(term) // 2)]])
    return ranked_matches(term, (candidate(p) for p in installable(packages)))


def install_package(worker, candidate, progress):
    client, pk = package_client(candidate.source)
    parts = candidate.target.split(";")
    if len(parts) != 4 or parts[0] != candidate.name:
        raise ManagementError(_("The selected package is invalid. Search again."))

    def report(status, _kind, _data):
        value = status.get_percentage()
        progress(
            _("Installing %s") % candidate.name,
            value / 100 if 0 <= value <= 100 else None,
            status.get_allow_cancel(),
        )

    progress(_("Installing %s") % candidate.name, None, True)
    worker.check_cancelled()
    # PackageKit handles dependencies and the desktop Polkit authorization agent.
    # Never accept untrusted packages or invoke a shell / collect credentials.
    try:
        result = client.install_packages(
            1 << int(pk.TransactionFlagEnum.ONLY_TRUSTED),
            [candidate.target],
            worker.cancel,
            report,
            None,
        )
        checked_packages(result, pk)
    except GLib.Error:
        worker.check_cancelled()
        raise
    return OperationResult(
        Outcome.SUCCESS, _("The system package was installed."), (candidate.name,)
    )


def remote_candidates(fp, worker, installation, remote, scope):
    """The remote's applications, reusing a recent enumeration of the same catalogue."""
    key = (installation.get_path().get_path(), remote.get_name())
    now = time.monotonic()
    for stale in [k for k, (stamp, _refs) in _remote_refs.items() if now - stamp >= REMOTE_REF_TTL]:
        del _remote_refs[stale]
    cached = _remote_refs.get(key)
    if cached is not None:
        return cached[1]
    candidates = tuple(
        InstallCandidate(
            "flatpak",
            ref.get_name(),
            ref.format_ref(),
            " · ".join((remote.get_name(), scope, ref.get_branch())),
            key[0],
            key[1],
        )
        for ref in installation.list_remote_refs_sync(remote.get_name(), worker.cancel)
        if ref.get_kind() == fp.RefKind.APP and ref.get_arch() == fp.get_default_arch()
    )
    _remote_refs[key] = (now, candidates)
    return candidates


def remote_catalogue(installation, remote):
    """A remote's AppStream apps as (pool, {ref: component}, directory), or None.

    Flatpak downloads the catalogue when the remote is updated; searching never does.
    """
    As = load_appstream()
    location = remote.get_appstream_dir(None)
    if As is None or location is None:
        return None
    directory = Path(location.get_path())
    catalogue = directory / "appstream.xml.gz"
    try:
        # Flatpak publishes a new catalogue by replacing the "active" symlink.
        info = catalogue.stat()
        stamp = (str(directory.resolve()), info.st_mtime_ns, info.st_size)
    except OSError:
        return None
    key = (installation.get_path().get_path(), remote.get_name())
    cached = _flatpak_catalogues.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    metadata = As.Metadata()
    metadata.set_format_style(As.FormatStyle.CATALOG)
    try:
        metadata.parse_file(Gio.File.new_for_path(str(catalogue)), As.FormatKind.XML)
    except GLib.Error:
        return None
    components = metadata.get_components()
    apps = {}
    for component in components.as_array():
        bundle = component.get_bundle(As.BundleKind.FLATPAK)
        if component.get_kind() == As.ComponentKind.DESKTOP_APP and bundle and bundle.get_id():
            apps.setdefault(bundle.get_id(), component)
    pool = As.Pool()
    pool.set_flags(As.PoolFlags.NONE)
    pool.add_components(components)
    _flatpak_catalogues[key] = (stamp, (pool, apps, directory))
    return pool, apps, directory


def catalogue_candidate(candidate, component, directory):
    return replace(
        candidate,
        title=component.get_name() or "",
        icon=appstream_icon(component, directory),
        description=" · ".join(
            v for v in (component.get_summary(), candidate.name, candidate.description) if v
        ),
    )


def search_flatpak(worker, query):
    fp = load_flatpak()
    installations, warnings = configured_installations(fp)
    candidates, matches, catalogued = [], [], {}
    As = load_appstream()
    enumerated = 0
    for installation in installations:
        worker.check_cancelled()
        try:
            sources = installation.list_remotes(worker.cancel)
            installed = {
                ref.format_ref() for ref in installation.list_installed_refs(worker.cancel)
            }
        except GLib.Error as error:
            worker.check_cancelled()
            warnings.append(str(error))
            continue
        scope = (
            _("User")
            if installation.get_is_user()
            else (_("System") + " · " + (installation.get_id() or "default"))
        )
        for remote in sources:
            if remote.get_disabled() or remote.get_noenumerate():
                continue
            worker.check_cancelled()
            try:
                available = remote_candidates(fp, worker, installation, remote, scope)
            except GLib.Error as error:
                worker.check_cancelled()
                warnings.append(str(error))
                continue
            enumerated += 1
            refs = {c.target: c for c in available if c.target not in installed}
            candidates.extend(refs.values())
            catalogue = remote_catalogue(installation, remote)
            if catalogue is None:
                continue
            pool, apps, directory = catalogue
            for ref in refs.keys() & apps.keys():
                catalogued[refs[ref]] = (apps[ref], directory)
            # Search names, summaries and keywords like native search, in relevance order.
            for rank, component in enumerate(pool.search(query).as_array()):
                bundle = component.get_bundle(As.BundleKind.FLATPAK)
                ref = bundle.get_id() if bundle else ""
                if ref in refs and ref in apps:
                    # Reverse-DNS IDs end in words like "Application"; only the name counts.
                    named = named_first(query, apps[ref].get_name() or "", "")
                    matches.append((named, rank, refs[ref]))
    # A source that could not be read is only an error when nothing could be searched;
    # otherwise a query with no match is reported as a plain absence of results.
    if not enumerated:
        raise ManagementError(
            "\n".join(warnings)
            if warnings
            else _(
                "No Flatpak sources are configured. Add or enable a source in Preferences → Software Sources, then try again."
            )
        )
    if matches:
        found = tuple(dict.fromkeys(row[-1] for row in sorted(matches, key=lambda row: row[:2])))
    else:
        # No catalogue match, e.g. a typo or an app without AppStream data: rank app IDs
        # by the longest word, since IDs hold no phrases.
        found = ranked_matches(search_term(query), candidates)
    # Only the shown results read icon files; the catalogue names every listed app.
    return tuple(
        catalogue_candidate(c, *catalogued[c]) if c in catalogued else c for c in found[:12]
    )


def install_flatpak(worker, candidate, progress):
    fp = load_flatpak()
    installations, _warnings = configured_installations(fp)
    installation = next(
        (i for i in installations if i.get_path().get_path() == candidate.context), None
    )
    if installation is None:
        raise ManagementError(_("The Flatpak installation changed. Search again."))
    remotes = {
        r.get_name() for r in installation.list_remotes(worker.cancel) if not r.get_disabled()
    }
    if candidate.remote not in remotes or not candidate.target.startswith(
        "app/" + candidate.name + "/"
    ):
        raise ManagementError(_("The Flatpak source changed. Search again."))
    tx = fp.Transaction.new_for_installation(installation, worker.cancel)
    # Reuse already deployed system runtimes, including for per-user applications.
    tx.add_default_dependency_sources()
    tx.set_no_interaction(False)
    tx.set_disable_prune(True)
    tx.set_disable_auto_pin(False)
    tx.connect("add-new-remote", lambda *_args: False)

    def choose_remote(_tx, _ref, _runtime, choices):
        # A runtime is often offered by several remotes (flathub and flathub-beta);
        # prefer the remote the user selected, then Flatpak's priority order.
        preferred = [candidate.remote, *(c for c in choices if c in remotes)]
        return next((choices.index(c) for c in preferred if c in choices), -1)

    tx.connect("choose-remote-for-ref", choose_remote)
    tx.connect("ready", lambda _tx: not worker.cancel.is_cancelled())
    completed, errors = [], []

    def started(_tx, _op, status):
        def changed(p):
            progress(
                p.get_status() or _("Installing Flatpak"),
                None if p.get_is_estimating() else p.get_progress() / 100,
                True,
            )

        status.connect("changed", changed)
        changed(status)

    def failed(_tx, _op, error, _details):
        errors.append(str(error))
        return False

    tx.connect("new-operation", started)
    tx.connect("operation-done", lambda _tx, op, _commit, _result: completed.append(op.get_ref()))
    tx.connect("operation-error", failed)
    tx.add_install(candidate.remote, candidate.target, None)
    progress(_("Installing %s") % candidate.name, None, True)
    success = False
    try:
        worker.check_cancelled()
        success = tx.run(worker.cancel)
    except (GLib.Error, OperationCancelled) as error:
        errors.append(str(error))
    if success:
        return OperationResult(
            Outcome.SUCCESS, _("The Flatpak application was installed."), tuple(completed)
        )
    outcome = (
        Outcome.PARTIAL
        if completed
        else (Outcome.CANCELLED if worker.cancel.is_cancelled() else Outcome.FAILED)
    )
    return OperationResult(
        outcome, _("Flatpak installation did not finish."), tuple(completed), tuple(errors)
    )
