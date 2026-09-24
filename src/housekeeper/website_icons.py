"""Best-effort website icons, fetched only during installation and stored locally."""

import re
import tempfile
import time
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urljoin, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gi.repository import GLib

from housekeeper.appearance import store_icon_image
from housekeeper.models import ManagementError, OperationCancelled
from housekeeper.web_identity import checked_http_url

PAGE_LIMIT = 512 * 1024
ICON_LIMIT = 2 * 1024 * 1024
FETCH_BUDGET = 8
FALLBACK_ICON = "web-browser"


def _http_url(value):
    parsed, host = checked_http_url(value)
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is not None:
        host += f":{parsed.port}"
    return urlunsplit(
        (
            parsed.scheme,
            host,
            quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~"),
            quote(parsed.query, safe="%/?@:!$&'()*+,;=-._~"),
            "",
        )
    )


class _Icons(HTMLParser):
    def __init__(self, page_url):
        super().__init__()
        self.base = page_url
        self.has_base = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href")
        if not href:
            return
        if tag == "base" and not self.has_base:
            self.base = urljoin(self.base, href.strip())
            self.has_base = True
        elif tag == "link" and len(self.links) < 32:
            rel = (attrs.get("rel") or "").lower().split()
            if not {"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}.intersection(rel):
                return
            sizes = (attrs.get("sizes") or "").lower().split()
            size = max(
                (
                    min(int(w), int(h), 512)
                    for w, h in re.findall(r"\b(\d{1,4})x(\d{1,4})\b", " ".join(sizes))
                ),
                default=180 if "apple-touch-icon" in rel else 32,
            )
            self.links.append((512 if "any" in sizes else size, href.strip()))

    def candidates(self):
        return list(
            dict.fromkeys(
                urljoin(self.base, href)
                for _size, href in sorted(self.links, key=lambda link: link[0], reverse=True)
            )
        )[:4]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch(opener, url, limit, check, *, page=False):
    for _redirect in range(4):
        url = _http_url(url)
        timeout = min(2, check())
        request = Request(url, headers={"User-Agent": "Housekeeper", "Accept-Encoding": "identity"})
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as error:
            with error:
                if error.code not in {301, 302, 303, 307, 308} or not error.headers.get("Location"):
                    raise
                url = urljoin(url, error.headers["Location"])
            continue
        with response:
            if not page and int(response.headers.get("Content-Length", "0")) > limit:
                raise ValueError("Website icon is too large")
            chunks, total = [], 0
            while total < limit + (not page):
                check()
                chunk = response.read1(min(64 * 1024, limit + (not page) - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            check()
            if total > limit:
                raise ValueError("Website icon is too large")
            return b"".join(chunks), url, response.headers.get_content_charset() or "utf-8"
    raise ValueError("Too many website redirects")


def website_icon(url, check_cancelled, created=None):
    """Prefer declared icons, then /favicon.ico; errors leave a usable generic icon."""
    deadline = time.monotonic() + FETCH_BUDGET

    def check():
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Website icon lookup timed out")
        return remaining

    opener = build_opener(_NoRedirect())
    errors = (OSError, ValueError, HTTPException, GLib.Error, ManagementError)
    candidates = []
    try:
        page, url, encoding = _fetch(opener, url, PAGE_LIMIT, check, page=True)
        parser = _Icons(url)
        try:
            text = page.decode(encoding, errors="replace")
        except LookupError:
            text = page.decode("utf-8", errors="replace")
        parser.feed(text)
        candidates = parser.candidates()
    except OperationCancelled:
        raise
    except errors:
        pass
    candidates.append(urljoin(url, "/favicon.ico"))
    for candidate in dict.fromkeys(candidates):
        try:
            contents, _url, _encoding = _fetch(opener, candidate, ICON_LIMIT, check)
            with tempfile.TemporaryDirectory(prefix="housekeeper-website-icon-") as directory:
                image = Path(directory) / "icon"
                image.write_bytes(contents)
                check()
                return str(store_icon_image(image, created))
        except OperationCancelled:
            raise
        except errors:
            continue
    check_cancelled()
    return FALLBACK_ICON
