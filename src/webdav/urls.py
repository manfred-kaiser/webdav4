"""URLs parsing logics here."""

from posixpath import normpath
from re import sub

from httpx import URL


def strip_trailing_slash(path: str) -> str:
    """Strips trailing slash from the path, except when it's a root."""
    return path.rstrip("/") if path and path != "/" else path


def normalize_path(path: str) -> str:
    """Normalizes path, removes trailing slash."""
    path = sub("/{2,}", "/", path)
    return strip_trailing_slash(path)


def join_url(base_url: URL, path: str, add_trailing_slash: bool = False) -> URL:
    """Joins base url with a path."""
    base_path = base_url.path
    path = join_url_path(base_path, path)
    if add_trailing_slash:
        path += "/"
    return base_url.copy_with(path=path)


def join_url_path(hostname: str, path: str) -> str:
    """Returns path absolute."""
    path = path.strip("/")
    return normalize_path(f"/{hostname}/{path}")


def relative_url_to(base_url: URL, rel: str) -> str:
    """Finds relative url to a base url path.

    Raises ValueError if ``rel`` does not resolve to a path under
    ``base_url`` - a server response should never point outside of what
    was requested. Also rejects embedded NUL bytes and backslashes, which
    ``posixpath.normpath`` does not treat as separators but which a
    downstream consumer joining this onto a real filesystem path (e.g. on
    Windows) might.
    """
    if "\x00" in rel or "\\" in rel:
        raise ValueError(f"{rel!r} contains an unexpected NUL byte or backslash")

    base = normpath(f"/{base_url.path.strip('/')}").strip("/")
    rel = normpath(f"/{rel.strip('/')}").strip("/")

    if base == rel or not rel:
        return "/"

    if not base and rel:
        return rel

    if not rel.startswith(f"{base}/"):
        raise ValueError(f"{rel!r} is not a subpath of {base!r}")

    index = len(base) + 1
    return rel[index:]
