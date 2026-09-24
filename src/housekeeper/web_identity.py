"""Validate website URLs and match Chromium's URL-app identity on Wayland and X11."""

import hashlib
import re
from urllib.parse import quote, urlsplit

PROFILE = "Default"


def checked_http_url(value):
    """Split an http(s) URL and return it with its ASCII host, or raise ValueError.

    Shared by typed launcher URLs and fetched icon/redirect URLs so both refuse the
    same credentials, backslash and control-character tricks.
    """
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Control characters in URL")
    url = urlsplit(value)
    if (
        url.scheme not in {"https", "http"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or "\\" in url.netloc
    ):
        raise ValueError("Not an http or https URL without credentials")
    _port = url.port
    # UnicodeError, raised for an invalid IDNA host, is a ValueError.
    host = url.hostname.encode("idna").decode("ascii")
    if ":" not in host and not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
        raise ValueError("Invalid host name")
    return url, host


def legacy_identifier(browser, url):
    return "housekeeper-web-" + hashlib.sha256((browser + "\n" + url).encode()).hexdigest()[:24]


def window_identity(url):
    # Chromium: GenerateApplicationNameFromURL, GetWMClassFromAppName and
    # GetAppDesktopShortcutFilename. --class does not override a Wayland app ID.
    parsed = urlsplit(url)
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = f"[{host}]"
    path = quote(parsed.path.replace("\\", "/") or "/", safe="/%!$&'()*+,-.:;=@_~")
    segments = []
    for segment in path.split("/"):
        dot = re.sub("%2e", ".", segment, flags=re.IGNORECASE)
        if dot == "..":
            if len(segments) > 1:
                segments.pop()
        elif dot != ".":
            segments.append(segment)
    if dot in {".", ".."}:
        segments.append("")
    path = "/".join(segments) or "/"
    app_name = re.sub(r'["*/:<>?\\|]', "_", host + "_" + path)
    # X11 strips leading/trailing underscores; the Wayland desktop ID does not.
    return "chrome-" + app_name + "-" + PROFILE, app_name.strip("_")


def launch_args(browser, url):
    # Pin the profile so a different last-used Chrome profile cannot change app_id.
    return browser, "--profile-directory=" + PROFILE, "--app=" + url
