"""Website icons use declared resources, bounded HTTP requests and durable PNGs."""

import io
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from housekeeper import website_icons as icons
from housekeeper.models import OperationCancelled

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="blue"/></svg>'
SITE = "https://example.org/notes"


class Response(io.BytesIO):
    def __init__(self, data, **headers):
        super().__init__(data)
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    resources, requested = {}, []

    def open_request(request, timeout):
        assert 0 < timeout <= 2
        assert request.get_header("Accept-encoding") == "identity"
        requested.append(request.full_url)
        response = resources.get(request.full_url, OSError("not found"))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(icons, "build_opener", lambda *_: Mock(open=open_request))
    return resources, requested


def test_redirect_base_relative_icons_and_prefer_larger_image(web):
    resources, requested = web
    resources[SITE] = HTTPError(SITE, 302, "Found", {"Location": "/new/page"}, io.BytesIO())
    resources["https://example.org/new/page"] = Response(
        b'<base href="../assets/"><link rel="shortcut ICON" href="small.ico" sizes="16x16">'
        b'<link rel="apple-touch-icon" href="large.svg" sizes="180x180">'
        b'<link rel="mask-icon" href="mask.svg">'
    )
    resources["https://example.org/assets/large.svg"] = Response(SVG)
    result = Path(icons.website_icon(SITE, lambda: None))
    assert result.read_bytes().startswith(b"\x89PNG")
    assert result.parent.name == "icons" and result.parent.parent.name == "housekeeper"
    assert requested == [
        SITE,
        "https://example.org/new/page",
        "https://example.org/assets/large.svg",
    ]


def test_invalid_declared_image_falls_back_to_origin_favicon(web):
    resources, requested = web
    resources[SITE] = Response(b'<link rel="icon" href="bad.png">')
    resources["https://example.org/bad.png"] = Response(b"not an image")
    resources["https://example.org/favicon.ico"] = Response(SVG)
    assert Path(icons.website_icon(SITE, lambda: None)).exists()
    assert requested[-2:] == ["https://example.org/bad.png", "https://example.org/favicon.ico"]


@pytest.mark.parametrize("failure", [OSError("offline"), TimeoutError("timeout"), b"no icon here"])
def test_missing_or_unavailable_icons_use_generic_fallback(web, failure):
    resources, _requested = web
    resources[SITE] = Response(failure) if isinstance(failure, bytes) else failure
    assert icons.website_icon(SITE, lambda: None) == "web-browser"


@pytest.mark.parametrize(
    "target", ["file:///etc/passwd", "ftp://example.org/icon", "https://u:p@example.org/icon"]
)
def test_non_http_or_credentialed_resources_and_redirects_are_not_opened(web, target):
    resources, requested = web
    resources[SITE] = Response(f'<link rel="icon" href="{target}">'.encode())
    fallback = "https://example.org/favicon.ico"
    resources[fallback] = HTTPError(fallback, 302, "Found", {"Location": target}, io.BytesIO())
    assert icons.website_icon(SITE, lambda: None) == "web-browser"
    assert requested == [SITE, fallback]


@pytest.mark.parametrize("header", [True, False])
def test_oversized_icon_is_rejected_before_decode(web, monkeypatch, header):
    resources, _requested = web
    resources[SITE] = Response(b"")
    resources["https://example.org/favicon.ico"] = Response(
        b"x" * (icons.ICON_LIMIT + 1),
        **({"Content-Length": str(icons.ICON_LIMIT + 1)} if header else {}),
    )
    store = Mock()
    monkeypatch.setattr(icons, "store_icon_image", store)
    assert icons.website_icon(SITE, lambda: None) == "web-browser"
    store.assert_not_called()


def test_cancellation_is_not_swallowed_as_a_fallback(web):
    _resources, requested = web

    def cancelled():
        raise OperationCancelled("cancelled")

    with pytest.raises(OperationCancelled):
        icons.website_icon(SITE, cancelled)
    assert not requested


def test_time_budget_stops_additional_requests(web, monkeypatch):
    _resources, requested = web
    clock = iter((0, icons.FETCH_BUDGET + 1, icons.FETCH_BUDGET + 2))
    monkeypatch.setattr(icons.time, "monotonic", lambda: next(clock))
    assert icons.website_icon(SITE, lambda: None) == "web-browser"
    assert not requested


def test_real_http_redirect_and_icon_download(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/app/page")
                self.end_headers()
                return
            content = SVG if self.path == "/app/icon.svg" else b'<link rel="icon" href="icon.svg">'
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            result = icons.website_icon(
                f"http://127.0.0.1:{server.server_port}/start", lambda: None
            )
            assert Path(result).read_bytes().startswith(b"\x89PNG")
            assert requested == ["/start", "/app/page", "/app/icon.svg"]
        finally:
            server.shutdown()
            worker.join()
