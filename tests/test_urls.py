"""Testing url parsing and processing logic."""

import pytest

from webdav4.urls import (
    URL,
    join_url,
    join_url_path,
    normalize_path,
    relative_url_to,
    strip_trailing_slash,
)


@pytest.mark.parametrize(
    "base_url, path, expected",
    [
        ("https://example.org", "/", "https://example.org/"),
        ("https://example.org/", "/", "https://example.org/"),
        ("https://example.org", "", "https://example.org/"),
        ("https://example.org/", "", "https://example.org/"),
        ("https://example.org", "path", "https://example.org/path"),
        ("https://example.org/", "path", "https://example.org/path"),
        ("https://example.org/", "/path", "https://example.org/path"),
        ("https://example.org/", "/path/", "https://example.org/path"),
        ("https://example.org/", "path/", "https://example.org/path"),
        ("https://example.org/foo", "bar", "https://example.org/foo/bar"),
        ("https://example.org/foo/", "bar", "https://example.org/foo/bar"),
        ("https://example.org/foo/", "/bar", "https://example.org/foo/bar"),
        ("https://example.org/foo", "/bar", "https://example.org/foo/bar"),
        ("https://example.org/foo/", "/bar/", "https://example.org/foo/bar"),
    ],
)
def test_join_url(base_url: str, path: str, expected: str):
    """Testing join_url operation in the client."""
    assert join_url(URL(base_url), path) == expected


@pytest.mark.parametrize(
    "base, rel, expected",
    [
        ("https://example.org/foo", "foo", "/"),
        ("https://example.org/foo", "/foo", "/"),
        ("https://example.org/foo", "foo/", "/"),
        ("https://example.org/foo", "/foo/", "/"),
        ("https://example.org/foo/", "foo", "/"),
        ("https://example.org/foo/", "/foo", "/"),
        ("https://example.org/foo/", "foo/", "/"),
        ("https://example.org/foo/", "/foo/", "/"),
        ("https://example.org", "/", "/"),
        ("https://example.org/", "/", "/"),
        ("https://example.org", "", "/"),
        ("https://example.org/", "", "/"),
        ("https://example.org", "data", "data"),
        ("https://example.org", "/data", "data"),
        ("https://example.org", "data/", "data"),
        ("https://example.org", "/data/", "data"),
        ("https://example.org/", "data", "data"),
        ("https://example.org/", "/data", "data"),
        ("https://example.org/", "data/", "data"),
        ("https://example.org/", "/data/", "data"),
        ("https://example.org/foo", "foo/bar", "bar"),
        ("https://example.org/foo", "/foo/bar", "bar"),
        ("https://example.org/foo", "foo/bar/", "bar"),
        ("https://example.org/foo", "/foo/bar/", "bar"),
        ("https://example.org/foo/", "foo/bar", "bar"),
        ("https://example.org/foo/", "/foo/bar", "bar"),
        ("https://example.org/foo/", "foo/bar/", "bar"),
        ("https://example.org/foo/", "/foo/bar/", "bar"),
    ],
)
def test_path_relative_to(base: str, rel: str, expected: str):
    """Test relative path calculation."""
    assert relative_url_to(URL(base), rel) == expected


@pytest.mark.parametrize(
    "base, rel",
    [
        ("https://example.org/dav", "/dav/../../../etc/cron.d/evil"),
        ("https://example.org/dav", "/dav/../../etc/passwd"),
        ("https://example.org/dav", "/davish/other"),
        ("https://example.org/dav", "/other"),
        ("https://example.org/dav/sub", "/dav/../evil"),
        ("https://example.org/dav", "/dav/sub\x00.txt"),
        ("https://example.org/dav", "/dav/..\\..\\evil"),
    ],
)
def test_path_relative_to_rejects_escaping_href(base: str, rel: str):
    """A server-supplied href outside of the requested base must be rejected."""
    with pytest.raises(ValueError):
        relative_url_to(URL(base), rel)


def test_path_relative_to_rejects_percent_encoded_traversal():
    """A percent-encoded ``..`` in the href, decoded the same way the real
    call site (``URL(href).path``) does it, must also be rejected.
    """
    base = URL("https://example.org/dav")
    href = URL("https://example.org/dav/%2e%2e/%2e%2e/etc/passwd")
    with pytest.raises(ValueError):
        relative_url_to(base, href.path)


@pytest.mark.parametrize(
    "base_path, path, expected",
    [
        ("", "", "/"),
        ("", "/", "/"),
        ("/", "", "/"),
        ("/", "/", "/"),
        ("", "foo", "/foo"),
        ("", "/foo", "/foo"),
        ("", "foo/", "/foo"),
        ("", "/foo/", "/foo"),
        ("/", "foo", "/foo"),
        ("/", "/foo", "/foo"),
        ("/", "foo/", "/foo"),
        ("/", "/foo/", "/foo"),
        ("/foo", "bar", "/foo/bar"),
        ("/foo", "/bar", "/foo/bar"),
        ("/foo", "bar/", "/foo/bar"),
        ("/foo", "/bar/", "/foo/bar"),
        ("foo/", "bar", "/foo/bar"),
        ("foo/", "/bar", "/foo/bar"),
        ("foo/", "bar/", "/foo/bar"),
        ("foo/", "/bar/", "/foo/bar"),
        ("foo", "bar", "/foo/bar"),
        ("foo", "/bar", "/foo/bar"),
        ("foo", "bar/", "/foo/bar"),
        ("foo", "/bar/", "/foo/bar"),
        ("/foo/", "bar", "/foo/bar"),
        ("/foo/", "/bar", "/foo/bar"),
        ("/foo/", "bar/", "/foo/bar"),
        ("/foo/", "/bar/", "/foo/bar"),
        ("/foo/bar", "foobar", "/foo/bar/foobar"),
        ("/foo/bar/", "foobar", "/foo/bar/foobar"),
        ("/foo/bar", "foobar/", "/foo/bar/foobar"),
        ("/foo/bar", "foobar/foobar", "/foo/bar/foobar/foobar"),
    ],
)
def test_join_url_path(base_path: str, path: str, expected: str):
    """Test joining base path and path together, while normalizing the url."""
    assert join_url_path(base_path, path) == expected


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/", "/"),
        ("foo", "foo"),
        ("/foo", "/foo"),
        ("/foo/bar/", "/foo/bar"),
        ("/foo//bar//", "/foo/bar"),
        ("/////foo////bar////", "/foo/bar"),
    ],
)
def test_normalize_url(path: str, expected: str):
    """Test that it normalizes urls removing too many "/" and leading slash."""
    assert normalize_path(path) == expected


@pytest.mark.parametrize(
    "path, expected",
    [
        ("", ""),
        ("/", "/"),
        ("foo", "foo"),
        ("foo/bar/", "foo/bar"),
        ("/foo/bar/", "/foo/bar"),
    ],
)
def test_strip_leading_slash(path: str, expected: str):
    """Test that it strips leading slash, except the "/" only urls."""
    assert strip_trailing_slash(path) == expected
