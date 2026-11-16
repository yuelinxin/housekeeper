#!/bin/bash
set -euo pipefail

if [[ ! -e /run/.containerenv && ! -e /.dockerenv ]]; then
    echo 'This script requires a disposable container.' >&2
    exit 1
fi
source /etc/os-release
test "$ID" = arch
housekeeper_version=$(PYTHONPATH=/source/src /usr/bin/python -c 'from housekeeper import VERSION; print(VERSION)')
housekeeper_check=$(mktemp -d /tmp/housekeeper-arch.XXXXXX)
python /source/build-aux/arch_package.py --output "$housekeeper_check"
chown -R hk-build:hk-build "$housekeeper_check"
cd "$housekeeper_check"
runuser -u hk-build -- makepkg --cleanbuild --noconfirm --log
runuser -u hk-build -- makepkg --printsrcinfo > .SRCINFO
housekeeper_source="$housekeeper_check/src/housekeeper-${housekeeper_version}"
for desktop in GNOME KDE; do
    runuser -u hk-build -- env \
        XDG_CURRENT_DESKTOP="$desktop" HOUSEKEEPER_BUILD_DIR="$housekeeper_check/src/build" \
        GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
        xvfb-run -a dbus-run-session --config-file="$housekeeper_source/tests/session.conf" -- \
        /usr/bin/python "$housekeeper_source/tests/smoke_ui.py"
done
runuser -u hk-build -- env GTK_A11Y=none GIO_USE_VFS=local GSK_RENDERER=cairo \
    xvfb-run -a dbus-run-session --config-file="$housekeeper_source/tests/session.conf" -- \
    /usr/bin/python "$housekeeper_source/tests/stress_icons.py" --runs 5
packages=("$housekeeper_check"/housekeeper-*-any.pkg.tar.zst)
test "${#packages[@]}" = 1
pacman -U --noconfirm "${packages[0]}"
cd /tmp
runuser -u hk-build -- python /source/tests/integration_arch_runtime.py
pacman -U --noconfirm "${packages[0]}"
runuser -u hk-build -- housekeeper --version
pacman -R --noconfirm housekeeper
test ! -e /usr/bin/housekeeper
test ! -e /usr/share/housekeeper
test ! -e /usr/share/applications/io.github.yuelinxin.housekeeper.desktop
if [[ -d /artifacts ]]; then
    cp "${packages[0]}" "$housekeeper_check/PKGBUILD" "$housekeeper_check/.SRCINFO" \
        "$housekeeper_check/housekeeper-${housekeeper_version}-arch.tar.gz" /artifacts/
    cp "$housekeeper_check/src/build/meson-logs/testlog.txt" /artifacts/arch-unit-tests.txt
    pacman -Q > /artifacts/arch-environment.txt
fi
echo 'PASS: Arch makepkg, GNOME/KDE identity GTK smoke, install, replacement and removal'
