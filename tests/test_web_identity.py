"""Desktop files match the identifiers Chromium supplies to the compositor."""

import pytest

from housekeeper.web_identity import window_identity


@pytest.mark.parametrize(
    "url,desktop,wmclass",
    [
        ("https://apple.com/", "chrome-apple.com__-Default", "apple.com"),
        ("https://example.org/notes", "chrome-example.org__notes-Default", "example.org__notes"),
        (
            "https://EXAMPLE.org/a%20b?q=1#two",
            "chrome-example.org__a%20b-Default",
            "example.org__a%20b",
        ),
        ("https://example.org/one/../two", "chrome-example.org__two-Default", "example.org__two"),
        ("https://example.org/a/%2e%2e/", "chrome-example.org__-Default", "example.org"),
        (
            "https://example.org/café",
            "chrome-example.org__caf%C3%A9-Default",
            "example.org__caf%C3%A9",
        ),
    ],
)
def test_chromium_window_identifiers(url, desktop, wmclass):
    assert window_identity(url) == (desktop, wmclass)


def test_redirect_query_fragment_and_port_do_not_create_a_different_chromium_class():
    assert window_identity("http://example.org:8080/path?q=one#tab") == window_identity(
        "https://example.org/path?q=two"
    )
