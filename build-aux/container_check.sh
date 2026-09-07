#!/bin/bash
set -euo pipefail

# Run from a disposable container with this source tree mounted read-only at /source.
if [[ ! -e /run/.containerenv && ! -e /.dockerenv ]]; then
    echo 'This script requires a disposable container.' >&2
    exit 1
fi
test -f /source/dist/housekeeper-0.1.0.tar.gz
mkdir -p /tmp/housekeeper-check /tmp/housekeeper-rpm/SOURCES
tar -xzf /source/dist/housekeeper-0.1.0.tar.gz -C /tmp/housekeeper-check
cd /tmp/housekeeper-check/housekeeper-0.1.0
meson setup build --prefix=/usr
meson compile -C build
meson test -C build --print-errorlogs
GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
    xvfb-run -a dbus-run-session --config-file=tests/session.conf -- \
    /usr/bin/python3 tests/smoke_ui.py
cp /source/dist/housekeeper-0.1.0.tar.gz /tmp/housekeeper-rpm/SOURCES/
rpmbuild -ba --define '_topdir /tmp/housekeeper-rpm' packaging/housekeeper.spec
package=$(find /tmp/housekeeper-rpm/RPMS -name 'housekeeper-*.rpm' -print -quit)
rpm -i "$package"
cd /tmp
housekeeper --version
# Replacing the same version exercises installation scripts and retained preferences.
rpm -U --replacepkgs "$package"
housekeeper --version
rpm -e housekeeper
test ! -e /usr/bin/housekeeper
if [[ -d /artifacts ]]; then
    cp /tmp/housekeeper-rpm/RPMS/noarch/*.rpm /artifacts/
    cp /tmp/housekeeper-rpm/SRPMS/*.rpm /artifacts/
fi
echo 'PASS: clean build, GTK smoke, RPM install, replacement, and uninstall'
