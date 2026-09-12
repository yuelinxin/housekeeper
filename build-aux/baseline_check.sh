#!/bin/bash
set -euo pipefail

housekeeper_version=$(PYTHONPATH=/source/src /usr/bin/python3 -c 'from housekeeper import VERSION; print(VERSION)')
mkdir -p /tmp/housekeeper-baseline
tar -xzf "/source/dist/sources/housekeeper-${housekeeper_version}.tar.gz" -C /tmp/housekeeper-baseline
cd "/tmp/housekeeper-baseline/housekeeper-${housekeeper_version}"
meson setup build --prefix=/usr
meson compile -C build
PYTHONPATH=src /usr/bin/python3 -m pytest -q
GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
    xvfb-run -a dbus-run-session --config-file=tests/session.conf -- \
    /usr/bin/python3 tests/smoke_ui.py
GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
    xvfb-run -a dbus-run-session --config-file=tests/session.conf -- \
    /usr/bin/python3 tests/stress_icons.py --runs 5
echo 'PASS: conservative runtime without optional native or Flatpak integrations'
