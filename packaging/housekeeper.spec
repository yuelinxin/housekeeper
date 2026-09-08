Name:           housekeeper
Version:        0.1.5
Release:        1%{?dist}
Summary:        Understand and manage installed desktop applications
License:        MIT
URL:            https://github.com/yuelinxin/housekeeper
Source0:        %{url}/releases/download/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  meson >= 0.63
BuildRequires:  python3
BuildRequires:  python3-gobject
BuildRequires:  python3-pytest
BuildRequires:  gtk4 >= 4.12
BuildRequires:  libadwaita >= 1.4
BuildRequires:  glib2-devel
BuildRequires:  gettext
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
Requires:       python3 >= 3.10
Requires:       python3-gobject
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
Application details include icon themes, custom launcher icons and restoration.
Native source labels follow the distribution; unsupported providers remain manual.

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
