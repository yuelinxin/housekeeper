import pytest

from housekeeper.models import DesktopEntry


@pytest.fixture
def desktop(tmp_path):
    def make(name="Example", filename="example.desktop", root=None, **keys):
        root = tmp_path if root is None else root
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = {"Type": "Application", "Name": name, "Exec": "/usr/bin/true", **keys}
        path.write_text(
            "[Desktop Entry]\n" + "\n".join(f"{k}={v}" for k, v in fields.items()) + "\n"
        )
        return path

    return make


@pytest.fixture
def entry(tmp_path):
    def make(argv=("/usr/bin/true",), desktop_id="example.desktop", **kwargs):
        return DesktopEntry(
            desktop_id,
            tmp_path / desktop_id,
            "Example",
            argv=argv,
            executable=argv[0] if argv else "",
            **kwargs,
        )

    return make
