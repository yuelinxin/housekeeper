import pytest

from housekeeper.platforms import native_package_source


@pytest.mark.parametrize(
    "release,expected",
    [
        ({"ID": "fedora"}, ("rpm", "RPM")),
        ({"ID": "opensuse-tumbleweed"}, ("rpm", "RPM")),
        ({"ID": "rocky", "ID_LIKE": "rhel centos fedora"}, ("rpm", "RPM")),
        ({"ID": "ubuntu", "ID_LIKE": "debian"}, ("deb", "DEB")),
        ({"ID": "debian"}, ("deb", "DEB")),
        ({"ID": "derivative", "ID_LIKE": "ubuntu debian"}, ("deb", "DEB")),
        ({"ID": "arch"}, ("pacman", "Pacman")),
        ({"ID": "derivative", "ID_LIKE": "arch"}, ("pacman", "Pacman")),
        ({"ID": "alpine"}, ("apk", "APK")),
        ({"ID": "nixos"}, ("nix", "Nix")),
        ({"ID": "gentoo"}, ("portage", "Portage")),
        ({"ID": "void"}, ("xbps", "XBPS")),
        ({"ID": "solus"}, ("eopkg", "eopkg")),
        ({"ID": "guix"}, ("guix", "Guix")),
        ({"ID": "ubuntu", "ID_LIKE": "fedora"}, ("deb", "DEB")),
        ({"ID": "unknown", "ID_LIKE": "debian fedora"}, ("deb", "DEB")),
        ({"ID": "unknown", "ID_LIKE": "notdebian"}, ("native", "System Packages")),
        ({}, ("native", "System Packages")),
    ],
)
def test_native_package_source(release, expected):
    assert native_package_source(release) == expected


def test_unavailable_os_release(monkeypatch):
    def unavailable():
        raise OSError("No os-release")

    monkeypatch.setattr("housekeeper.platforms.platform.freedesktop_os_release", unavailable)
    assert native_package_source() == ("native", "System Packages")
