"""PackageKit and Flatpak installation from search results and downloaded files."""

import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from gi.repository import Gio, GLib

from housekeeper.i18n import _
from housekeeper.installations import (
    InstallCandidate,
    LocalPackage,
    named_first,
    ranked_matches,
    read_small_file,
    search_term,
)
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
                InstallCandidate(
                    "snap",
                    item["name"],
                    item["name"],
                    str(item.get("summary", "")),
                    title=item["title"] if isinstance(item.get("title"), str) else "",
                    # Snaps may declare the AppStream IDs that other sources use.
                    app_id=next(
                        (i for i in item.get("common-ids") or () if isinstance(i, str)), ""
                    ),
                    summary=str(item.get("summary", "")),
                    origin=_("Snap Store"),
                )
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


def appstream_icon(component, directory=None):
    """The largest existing icon file; cached names resolve in `directory`.

    Catalogues usually carry 64px and 128px copies. Install pages show the icon as
    large as an installed app's details, so the smaller copy would look blurred.
    """
    files = []
    for icon in component.get_icons():
        name = icon.get_filename() or ""
        scale = max(1, icon.get_scale())
        if name and not Path(name).is_absolute():
            # A remote's catalogue names cached icons relative to its icons directory.
            if directory is None or "/" in name or name.startswith("."):
                continue
            size = f"{icon.get_width()}x{icon.get_height()}" + (f"@{scale}" if scale > 1 else "")
            name = str(directory / "icons" / size / name)
        if name and Path(name).is_file():
            files.append((icon.get_width() * scale, name))
    return max(files)[1] if files else ""


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

    installed = sum(1 << int(v) for v in (pk.FilterEnum.INSTALLED, pk.FilterEnum.ARCH))

    def marked(packages, names):
        """Each name once, as {name: (package, installed)}.

        dnf5 also reports an installed package as not installed when a repository
        carries the same build, so installed names are resolved explicitly.
        """
        names = sorted(set(names))
        if not names:
            return {}
        present = {p.get_name(): p for p in call(client.resolve, installed, names)}
        found = {p.get_name(): (p, False) for p in packages if p.get_name() not in present}
        found.update((name, (p, True)) for name, p in present.items())
        return found

    def candidate(p, is_installed=False, app=None):
        # An installed package names its repository as "installed:updates".
        repo = (p.get_data() or "").removeprefix("installed:")
        repo = "" if repo == "installed" else repo
        details = (p.get_version(), p.get_arch(), repo)
        if app is None:
            description = (*details, p.get_summary())
        else:
            description = (app.get_summary(), p.get_name(), *details)
        return InstallCandidate(
            source,
            p.get_name(),
            p.get_id(),
            " · ".join(v for v in description if v),
            remote=repo,
            title=app.get_name() if app is not None else "",
            icon=appstream_icon(app) if app is not None else "",
            app_id=(app.get_id() or "") if app is not None else "",
            summary=(app.get_summary() if app is not None else p.get_summary()) or "",
            origin=repo,
            installed=is_installed,
        )

    # The catalogue lists real desktop apps, so plugins, libraries and -devel
    # packages never appear, and names, summaries and keywords all match.
    apps = appstream_apps(worker, query)
    listed = []
    if apps:
        packages = marked(call(client.resolve, available, [*apps]), apps)
        ranked = sorted(
            (item for item in apps.items() if item[0] in packages),
            key=lambda item: named_first(query, item[1].get_name(), item[0]),
        )
        listed = [candidate(*packages[name], app) for name, app in ranked]
        # Installed apps alone still look further, for what third-party repositories offer.
        if any(not c.installed for c in listed):
            return tuple(listed[:12])
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
    # Installed apps are listed too, marked, including any no repository carries now.
    local = call(client.search_names, installed | 1 << int(pk.FilterEnum.GUI), [term])
    found = marked([*packages, *local], [p.get_name() for p in (*packages, *local)])
    names = {c.name for c in listed}
    more = ranked_matches(term, (candidate(*found[n]) for n in found if n not in names))
    return (*listed, *more)[:12]


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
            app_id=ref.get_name(),
            origin=f"{remote.get_title() or remote.get_name()} · {scope}",
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
        app_id=component.get_id() or candidate.app_id,
        summary=component.get_summary() or "",
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
                ref.format_ref(): ref.get_origin()
                for ref in installation.list_installed_refs(worker.cancel)
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
            # Installed apps stay listed, marked, so search shows what is already here.
            # Another remote's copy of an installed ref cannot be installed beside it.
            refs = {
                c.target: replace(c, installed=True) if c.target in installed else c
                for c in available
                if installed.get(c.target, remote.get_name()) == remote.get_name()
            }
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


def flatpak_installation(fp, worker, context):
    installations, _warnings = configured_installations(fp)
    installation = next((i for i in installations if i.get_path().get_path() == context), None)
    if installation is None:
        raise ManagementError(_("The Flatpak installation changed. Search again."))
    return installation


def flatpak_targets(fp):
    """Installations a Flatpak file can go to, as (path, label), the user's first."""
    installations, warnings = configured_installations(fp)
    targets = tuple(
        (
            i.get_path().get_path(),
            _("User") if i.get_is_user() else _("System") + " · " + (i.get_id() or "default"),
        )
        for i in sorted(installations, key=lambda i: not i.get_is_user())
    )
    if not targets:
        raise ManagementError("\n".join(warnings) or _("No Flatpak installation is available."))
    return targets


def run_flatpak(worker, installation, add, label, progress, *, preferred="", new_remotes=False):
    """Run one Flatpak installation transaction; `add` queues its operation.

    `new_remotes` lets a reviewed file add the source it names, or one for its runtime.
    """
    fp = load_flatpak()
    remotes = {
        r.get_name() for r in installation.list_remotes(worker.cancel) if not r.get_disabled()
    }
    tx = fp.Transaction.new_for_installation(installation, worker.cancel)
    # Reuse already deployed system runtimes, including for per-user applications.
    tx.add_default_dependency_sources()
    tx.set_no_interaction(False)
    tx.set_disable_prune(True)
    tx.set_disable_auto_pin(False)
    tx.connect("add-new-remote", lambda *_args: new_remotes)

    def choose_remote(_tx, _ref, _runtime, choices):
        # A runtime is often offered by several remotes (flathub and flathub-beta);
        # prefer the remote the user selected, then Flatpak's priority order.
        ordered = [preferred, *(c for c in choices if c in remotes)]
        index = next((choices.index(c) for c in ordered if c in choices), -1)
        return 0 if index < 0 and new_remotes and choices else index

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
    add(tx)
    progress(_("Installing %s") % label, None, True)
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


def install_flatpak(worker, candidate, progress):
    fp = load_flatpak()
    installation = flatpak_installation(fp, worker, candidate.context)
    remotes = {
        r.get_name() for r in installation.list_remotes(worker.cancel) if not r.get_disabled()
    }
    if candidate.remote not in remotes or not candidate.target.startswith(
        "app/" + candidate.name + "/"
    ):
        raise ManagementError(_("The Flatpak source changed. Search again."))
    return run_flatpak(
        worker,
        installation,
        lambda tx: tx.add_install(candidate.remote, candidate.target, None),
        candidate.name,
        progress,
        preferred=candidate.remote,
    )


def keyfile_values(data, group):
    keyfile = GLib.KeyFile()
    try:
        keyfile.load_from_bytes(GLib.Bytes.new(data), GLib.KeyFileFlags.NONE)
        keys = keyfile.get_keys(group)[0]
    except GLib.Error:
        raise ManagementError(_("The file is not a valid Flatpak reference.")) from None
    return {key: keyfile.get_string(group, key) for key in keys}


def inspect_flatpakref(worker, path, fingerprint):
    data = read_small_file(path)
    values = keyfile_values(data, "Flatpak Ref")
    name, url = values.get("Name", ""), values.get("Url", "")
    if not name or not url:
        raise ManagementError(_("The Flatpak reference does not name an app and its source."))
    if values.get("IsRuntime", "").casefold() == "true":
        raise ManagementError(_("This file installs a runtime, not an app."))
    targets = flatpak_targets(load_flatpak())
    worker.check_cancelled()
    return LocalPackage(
        "flatpakref",
        path,
        values.get("Title") or name,
        name,
        summary=values.get("Comment", ""),
        version=values.get("Branch", ""),
        origin=urlsplit(url).hostname or url,
        # Without a key in the file, Flatpak cannot verify what the source serves.
        verified=bool(values.get("GPGKey")),
        adds_remote=values.get("SuggestRemoteName", ""),
        runtime_repo=values.get("RuntimeRepo", ""),
        installations=targets,
        fingerprint=fingerprint,
        data=data,
    )


def bundle_listing(bundle):
    """The bundle's own AppStream name and summary, when it carries readable metadata."""
    import gzip

    As = load_appstream()
    appstream = bundle.get_appstream()
    if As is None or appstream is None:
        return "", ""
    try:
        metadata = As.Metadata()
        metadata.set_format_style(As.FormatStyle.CATALOG)
        metadata.parse_data(gzip.decompress(appstream.get_data()).decode(), -1, As.FormatKind.XML)
        component = metadata.get_component()
    except (GLib.Error, OSError, UnicodeDecodeError, EOFError):
        return "", ""
    if component is None:
        return "", ""
    return component.get_name() or "", component.get_summary() or ""


def inspect_flatpak_bundle(worker, path, fingerprint):
    fp = load_flatpak()
    try:
        bundle = fp.BundleRef.new(Gio.File.new_for_path(path))
    except GLib.Error as error:
        raise ManagementError(_("The Flatpak bundle could not be read: %s") % error) from None
    if bundle.get_kind() != fp.RefKind.APP:
        raise ManagementError(_("This file installs a runtime, not an app."))
    worker.check_cancelled()
    title, summary = bundle_listing(bundle)
    # Bundles may carry 64px and 128px icons; the larger one stays sharp on the install page.
    icon = bundle.get_icon(128) or bundle.get_icon(64)
    origin = bundle.get_origin() or ""
    return LocalPackage(
        "flatpak-bundle",
        path,
        title or bundle.get_name(),
        bundle.get_name(),
        summary=summary,
        version=bundle.get_branch() or "",
        origin=urlsplit(origin).hostname or origin,
        # A bundle can only carry its own key, which proves nothing about its publisher.
        verified=False,
        runtime_repo=bundle.get_runtime_repo_url() or "",
        icon=icon.get_data() if icon is not None else b"",
        installations=flatpak_targets(fp),
        fingerprint=fingerprint,
    )


def install_flatpak_file(worker, request, progress):
    package = request.package
    fp = load_flatpak()
    installation = flatpak_installation(fp, worker, request.context)
    if package.kind == "flatpakref":
        # Install exactly the reviewed reference, never a re-read of the file.
        def add(tx):
            tx.add_install_flatpakref(GLib.Bytes.new(package.data))
    else:

        def add(tx):
            tx.add_install_bundle(Gio.File.new_for_path(package.path), None)

    return run_flatpak(worker, installation, add, package.title, progress, new_remotes=True)


SIGNATURE_ERRORS = ("BAD_GPG_SIGNATURE", "MISSING_GPG_SIGNATURE", "GPG_FAILURE")


def signature_rejected(pk, code):
    return code in {int(getattr(pk.ErrorEnum, name)) for name in SIGNATURE_ERRORS}


def inspect_package_file(worker, kind, path, fingerprint):
    native, title = native_package_source()
    if kind != native:
        raise ManagementError(
            _("This is a %(file)s package, but this system uses %(native)s packages.")
            % {"file": kind.upper(), "native": title}
        )
    client, pk = package_client(native)
    worker.check_cancelled()
    try:
        result = client.get_details_local([path], worker.cancel, lambda *_args: None, None)
        checked_packages(result, pk)
    except GLib.Error as error:
        worker.check_cancelled()
        raise ManagementError(_("The package file could not be read: %s") % error) from None
    details = result.get_details_array()
    if not details:
        raise ManagementError(_("The package file could not be read."))
    name, version, arch, *_rest = [*details[0].get_package_id().split(";"), "", "", ""]
    return LocalPackage(
        kind,
        path,
        name,
        name,
        summary=details[0].get_summary() or "",
        version=" · ".join(v for v in (version, arch) if v),
        fingerprint=fingerprint,
    )


def install_package_file(worker, request, progress):
    package = request.package
    client, pk = package_client(package.kind)

    def report(status, _kind, _data):
        value = status.get_percentage()
        progress(
            _("Installing %s") % package.name,
            value / 100 if 0 <= value <= 100 else None,
            status.get_allow_cancel(),
        )

    untrusted = _(
        "The package is not signed by a key this system trusts. To install it anyway, "
        "turn on Allow Unverified Package."
    )
    progress(_("Installing %s") % package.name, None, True)
    worker.check_cancelled()
    # Unverified packages need explicit consent here and their own Polkit authorization.
    flags = 0 if request.untrusted else 1 << int(pk.TransactionFlagEnum.ONLY_TRUSTED)
    try:
        result = client.install_files(flags, [package.path], worker.cancel, report, None)
    except GLib.Error as error:
        worker.check_cancelled()
        # PackageKit reports a transaction error as its client error code plus 0xff.
        if error.domain == GLib.quark_to_string(pk.client_error_quark()) and (
            signature_rejected(pk, error.code - 0xFF)
        ):
            raise ManagementError(untrusted) from None
        raise
    error = result.get_error_code()
    if error and signature_rejected(pk, int(error.get_code())):
        raise ManagementError(untrusted)
    checked_packages(result, pk)
    return OperationResult(Outcome.SUCCESS, _("The system package was installed."), (package.name,))
