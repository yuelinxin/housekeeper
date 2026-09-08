"""Name the host's native package source without implying provider support."""

import platform

from housekeeper.i18n import _

_PACKAGE_FAMILIES = {
    "rpm": (
        "RPM",
        "fedora rhel centos rocky almalinux ol amzn suse opensuse opensuse-leap "
        "opensuse-tumbleweed sles sled mageia openmandriva mandriva pclinuxos",
    ),
    "deb": ("DEB", "debian ubuntu linuxmint pop elementary kali neon raspbian zorin deepin"),
    "pacman": ("Pacman", "arch archarm manjaro endeavouros artix cachyos garuda"),
    "apk": ("APK", "alpine postmarketos"),
    "nix": ("Nix", "nixos"),
    "portage": ("Portage", "gentoo funtoo"),
    "xbps": ("XBPS", "void"),
    "eopkg": ("eopkg", "solus"),
    "guix": ("Guix", "guix"),
}


def native_package_source(os_release=None):
    if os_release is None:
        try:
            os_release = platform.freedesktop_os_release()
        except OSError:
            os_release = {}
    # Prefer the actual distribution, then its closest declared relatives in order.
    for identity in [os_release.get("ID", ""), *os_release.get("ID_LIKE", "").split()]:
        for source, (title, identities) in _PACKAGE_FAMILIES.items():
            if identity.casefold() in identities.split():
                return source, title
    return "native", _("System Packages")
