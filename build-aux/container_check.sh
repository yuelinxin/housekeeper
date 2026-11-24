#!/bin/bash
set -euo pipefail

# Run from a disposable container with this source tree mounted read-only at /source.
if [[ ! -e /run/.containerenv && ! -e /.dockerenv ]]; then
    echo 'This script requires a disposable container.' >&2
    exit 1
fi
housekeeper_version=$(PYTHONPATH=/source/src /usr/bin/python3 -c 'from housekeeper import VERSION; print(VERSION)')
test -f "/source/dist/sources/housekeeper-${housekeeper_version}.tar.gz"
mkdir -p /tmp/housekeeper-check /tmp/housekeeper-rpm/SOURCES
tar -xzf "/source/dist/sources/housekeeper-${housekeeper_version}.tar.gz" -C /tmp/housekeeper-check
cd "/tmp/housekeeper-check/housekeeper-${housekeeper_version}"
meson setup build --prefix=/usr
meson compile -C build
meson test -C build --print-errorlogs
GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
    xvfb-run -a dbus-run-session --config-file=tests/session.conf -- \
    /usr/bin/python3 tests/smoke_ui.py
GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
    xvfb-run -a dbus-run-session --config-file=tests/session.conf -- \
    /usr/bin/python3 tests/stress_icons.py --runs 5
cp "/source/dist/sources/housekeeper-${housekeeper_version}.tar.gz" /tmp/housekeeper-rpm/SOURCES/
rpmbuild -ba --define '_topdir /tmp/housekeeper-rpm' packaging/housekeeper.spec
package=$(find /tmp/housekeeper-rpm/RPMS -name 'housekeeper-*.rpm' -print -quit)
# Fedora 45 rejects unsigned packages by default; this local build is unsigned.
rpm -i --nosignature "$package"
cd /tmp
housekeeper --version
# Replacing the same version exercises installation scripts and retained preferences.
rpm -U --nosignature --replacepkgs "$package"
housekeeper --version
# Packaged bytecode is owned by the RPM, so running as root adds no stray caches.
# Save the listing first: grep -q would close the pipe early and fail under pipefail.
rpm -qlp --nosignature "$package" > /tmp/housekeeper-files.txt
grep -q '^/usr/share/housekeeper/housekeeper/__pycache__/.*\.pyc$' /tmp/housekeeper-files.txt
rpm -e housekeeper
test ! -e /usr/bin/housekeeper
test ! -e /usr/share/housekeeper
if [[ -d /artifacts ]]; then
    cp /tmp/housekeeper-rpm/RPMS/noarch/*.rpm /artifacts/
    cp /tmp/housekeeper-rpm/SRPMS/*.rpm /artifacts/
fi
echo 'PASS: clean build, GTK smoke, RPM install, replacement, and uninstall'
