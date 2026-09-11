"""Test fixtures."""

from collections.abc import Iterator

import pytest
from cheroot import wsgi

from webdav import retry
from webdav.client import Client
from webdav.fsspec import WebdavFileSystem
from webdav.urls import URL

from .server import AUTH, get_server_address, run_server
from .utils import TmpDir


@pytest.fixture(autouse=True)
def reduce_backoff_factor():
    """Reduce backoff factor in tests."""
    backoff = retry.BACKOFF
    try:
        retry.BACKOFF = 0.001
        yield
    finally:
        retry.BACKOFF = backoff


@pytest.fixture
def auth() -> tuple[str, str]:
    """Auth for the server."""
    return AUTH


@pytest.fixture
def storage_dir(tmp_path_factory) -> TmpDir:
    """Storage for webdav server to keep files in."""
    path = tmp_path_factory.mktemp("webdav")
    return TmpDir(path)


@pytest.fixture
def server(
    storage_dir: TmpDir,
    auth: tuple[str, str],
) -> Iterator[wsgi.Server]:
    """Creates a server fixture for testing purpose."""
    with run_server("localhost", 0, str(storage_dir), auth) as (httpd, _):
        yield httpd


@pytest.fixture
def server_address(server: wsgi.Server) -> URL:
    """Address of the server to contact."""
    return get_server_address(server)


@pytest.fixture
def client(auth: tuple[str, str], server_address: URL) -> Client:
    """Webdav client to interact with the server."""
    return Client(server_address, auth=auth)


@pytest.fixture
def fs(client: Client, server_address: URL) -> WebdavFileSystem:
    """Fixture of WebdavFileSystem to interact with the server."""
    return WebdavFileSystem(server_address, client=client)
