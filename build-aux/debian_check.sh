#!/bin/bash
set -euo pipefail

if [[ ! -e /run/.containerenv && ! -e /.dockerenv ]]; then
    echo 'This script requires a disposable container.' >&2
    exit 1
fi
source /etc/os-release
case "${ID}:${VERSION_ID}" in
    ubuntu:24.04|debian:13) ;;
    *) echo 'Only Ubuntu 24.04 and Debian 13 are validated by this script.' >&2; exit 1 ;;
esac
housekeeper_version=$(PYTHONPATH=/source/src /usr/bin/python3 -c 'from housekeeper import VERSION; print(VERSION)')
housekeeper_check=$(mktemp -d /tmp/housekeeper-deb.XXXXXX)
tar -xzf "/source/dist/sources/housekeeper-${housekeeper_version}.tar.gz" -C "$housekeeper_check"
cd "$housekeeper_check/housekeeper-${housekeeper_version}"
housekeeper_deb_version=$(dpkg-parsechangelog -S Version)
case "$housekeeper_deb_version" in
    "${housekeeper_version}"-*) ;;
    *) echo 'Debian changelog and application versions differ.' >&2; exit 1 ;;
esac
# Distinct versions prevent release assets from two distributions colliding.
dch --newversion "${housekeeper_deb_version}+${ID}${VERSION_ID}" \
    --distribution "$VERSION_CODENAME" 'Build for the tested distribution.'
dpkg-buildpackage --no-sign -b
for desktop in GNOME KDE; do
    XDG_CURRENT_DESKTOP="$desktop" HOUSEKEEPER_BUILD_DIR="$PWD/build-deb" \
        GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
        xvfb-run -a dbus-run-session --config-file=tests/session.conf -- \
        /usr/bin/python3 tests/smoke_ui.py
done
HOUSEKEEPER_BUILD_DIR="$PWD/build-deb" GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
    xvfb-run -a dbus-run-session --config-file=tests/session.conf -- \
    /usr/bin/python3 tests/stress_icons.py --runs 5
packages=("$housekeeper_check"/housekeeper_*_all.deb)
test "${#packages[@]}" = 1
dpkg --install "${packages[0]}"
cd /tmp
python3 /source/tests/integration_deb_runtime.py
dpkg --install "${packages[0]}"
housekeeper --version
dpkg --purge housekeeper
test ! -e /usr/bin/housekeeper
test ! -e /usr/share/housekeeper
test ! -e /usr/share/applications/io.github.yuelinxin.housekeeper.desktop
if [[ -d /artifacts ]]; then
    cp "${packages[0]}" "$housekeeper_check"/*.buildinfo "$housekeeper_check"/*.changes /artifacts/
    cp "$housekeeper_check/housekeeper-${housekeeper_version}/build-deb/meson-logs/testlog.txt" \
        "/artifacts/${ID}${VERSION_ID}-unit-tests.txt"
fi
echo 'PASS: DEB build, GNOME/KDE session-identity GTK smoke, install, replacement and purge'
