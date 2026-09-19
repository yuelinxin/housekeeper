Name:           housekeeper
Version:        0.1.16
Release:        1%{?dist}
Summary:        Understand and manage installed desktop applications
License:        MIT
URL:            https://github.com/yuelinxin/housekeeper
Source0:        %{url}/releases/download/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  meson >= 0.63
BuildRequires:  python3
BuildRequires:  python3-gobject
BuildRequires:  gobject-introspection
BuildRequires:  python3-pytest
BuildRequires:  python3-rpm
BuildRequires:  flatpak-libs
BuildRequires:  gtk4 >= 4.12
BuildRequires:  libadwaita >= 1.4
BuildRequires:  glib2-devel
BuildRequires:  gettext
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       gobject-introspection
Requires:       gtk4 >= 4.12
Requires:       libadwaita >= 1.4
Recommends:     python3-rpm
Recommends:     PackageKit
Recommends:     PackageKit-glib
Recommends:     flatpak-libs

%description
Housekeeper provides a searchable GTK application inventory with installation
sources, file locations, and source-appropriate removal or management actions.
It supports RPM and Flatpak update previews and selected or all-app updates,
plus management of AppImages, browser web apps, and Steam shortcuts.
Application details include software sizes, icon themes, custom launcher icons
and restoration. Read-only attribution covers DEB, Pacman, APK and Snap packages.
Native source labels follow the distribution; unsupported providers remain manual.
Sort applications by name, size or last update, and configure update-check timing
and participating RPM and Flatpak sources in Preferences.

%prep
%autosetup

%build
%meson
%meson_build

%install
%meson_install

%check
%meson_test
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.yuelinxin.housekeeper.desktop
appstreamcli validate --no-net %{buildroot}%{_datadir}/metainfo/io.github.yuelinxin.housekeeper.metainfo.xml

%files
%license LICENSE
%doc README.md CHANGELOG.md CONTRIBUTING.md docs
%{_bindir}/housekeeper
%{_datadir}/housekeeper/
%{_datadir}/applications/io.github.yuelinxin.housekeeper.desktop
%{_datadir}/metainfo/io.github.yuelinxin.housekeeper.metainfo.xml
%{_datadir}/glib-2.0/schemas/io.github.yuelinxin.housekeeper.gschema.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.yuelinxin.housekeeper.svg
%{_datadir}/icons/hicolor/symbolic/apps/io.github.yuelinxin.housekeeper-symbolic.svg

%changelog
* Fri Sep 18 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.16-1
- Keep details, launching, update checks and management usable during inventory scans.
- Queue a management request behind a running scan and let Cancel withdraw it.
- Coalesce and rate-limit automatic refreshes, and retry a failed scan sooner.
- Keep exact removal targets and full update transactions in each confirmation's Details.

* Sun Sep 13 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.15-1
- Let users keep or delete Flatpak app data and permissions during uninstall.
- Preserve data by default and report incomplete cleanup separately.

* Sun Sep 13 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.14-1
- Open applications from their desktop entries, with a choice for multiple launchers.
- Show available updates beside application names and keep update actions on the Updates page.
- Open the selected Chrome or Chromium web app for management in its own menu.
- Refresh the application icon and prefer source-tree icons in development builds.

* Sat Sep 12 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.13-2
- Require the Cairo and other base typelibs needed to import GTK in clean buildroots.
- Include Flatpak and RPM bindings for the package test suite.

* Fri Sep 11 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.13-1
- Name the sandbox permissions a Flatpak update adds before granting them.
- Report running programs a package update would replace and stop for one started after the preview.
- Complete Flatpak updates containing a repair operation instead of reporting them as partial.
- Label an application's update action for what it will do once a check has found one.

* Fri Sep 11 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.12-1
- Honor cancellation during RPM and Flatpak removal setup.
- Harden D-Bus attribution, report dpkg failures, and retain AppImage management without package databases.
- Clarify shared application updates and preserve unrelated cache invalidation.
- Reduce repeated inventory work and repair static and GTK validation gates.

* Wed Sep 09 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.11-1
- Discover update candidates in batches and preview only matching desktop apps.
- Hide auxiliary launchers and runtime-only updates while retaining required dependencies.
- Simplify update confirmation and center its collapsed Details control.

* Wed Sep 09 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.10-1
- Verify Flatpak launchers and effective D-Bus services before direct management.
- Resolve ownership conflicts independently of provider order and fix duplicate entries.
- Improve XDG/env discovery and validate AppImage format and cross-backend ownership.

* Tue Sep 08 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.9-1
- Reorganize search, menu, refresh and sorting controls in the adaptive header.
- Add the Preferences shortcut and a remembered automatic inventory refresh switch.
- Dim hidden application icons and refresh README screenshots.

* Tue Sep 08 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.8-1
- Keep remaining cached updates visible after successful single and batch updates.
- Recognize RPM applications that use shared executables or D-Bus launchers.
- Verify RPM launcher contents and package relationships before direct management.

* Tue Sep 08 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.7-1
- Add remembered name, size and Last Updated sorting with Flatpak journal dates.
- Simplify the adaptive header and add update-check timing and source preferences.
- Scope cached update results to selected providers and rebuild changed settings.

* Mon Sep 07 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.6-1
- Show installed software sizes and add DEB, Pacman, APK and Snap attribution.
- Keep update-check progress, window layout and Cancel button states stable.

* Mon Sep 07 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.5-1
- Add custom launcher icons, restoration and GNOME refresh guidance.
- Show app appearance and adapt native source labels to the distribution.
- Move inventory spacing inside scrolling content and refresh screenshots.

* Mon Sep 07 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.4-1
- Cache update checks with a 24-hour TTL and retain results after cancellation.
- Move update actions to a compact adaptive footer and simplify page text.
- Unify list and grid highlights and remove extra hover layers from pill buttons.

* Mon Sep 07 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.3-1
- Add RPM and Flatpak updates, dependency previews, and selected/all-app updates.
- Fix system Flatpak authorization and enable cooperative download cancellation.
- Add diagnostics and automatically rebuild development resources before startup.

* Mon Sep 07 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.2-1
- Fix SVG icon-loading crashes and rename System Packages to RPM.
- Add repeated icon startup regression checks.

* Sun Sep 06 2026 Yuelin Xin <yuelinxin@users.noreply.github.com> - 0.1.0-1
- Initial development release.
