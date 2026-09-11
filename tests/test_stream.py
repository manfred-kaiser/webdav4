"""Testing stream utilities."""

from collections.abc import Iterator
from io import DEFAULT_BUFFER_SIZE
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from pytest import MonkeyPatch

from tests.utils import TmpDir
from webdav.client import Client
from webdav.fsspec import WebdavFileSystem
from webdav.http import HTTPNetworkError, HTTPResponse


def test_retry_reconnect_on_failure(
    storage_dir: TmpDir,
    fs: WebdavFileSystem,
    client: Client,
    monkeypatch: MonkeyPatch,
):
    """Test retry/reconnect on network failures."""
    original_iter_content = HTTPResponse.iter_bytes

    def bad_iter_content(
        response: "HTTPResponse", *args: Any, **kwargs: Any,
    ) -> Iterator[bytes]:
        """Simulate bad connection."""
        it = original_iter_content(response, *args, **kwargs)
        for i, chunk in enumerate(it):
            # Drop connection error on second chunk if there is one
            if i > 0:
                raise HTTPNetworkError(
                    "Simulated connection drop", request=response.request,
                )
            yield chunk

    # Text should be longer than default chunk to test resume,
    # using twice of that plus something tests second resume,
    # this is important because second response is different
    text1 = "0123456789" * (client.chunk_size // 10 + 1)
    storage_dir.gen("sample.txt", text1 * 2)
    propfind_resp = client.propfind("sample.txt")

    monkeypatch.setattr(HTTPResponse, "iter_bytes", bad_iter_content)

    with mock.patch.object(client, "propfind", return_value=propfind_resp):
        with client.open("sample.txt") as fd:
            # Test various .read() variants
            assert fd.read(len(text1)) == text1
            assert fd.read() == text1
            assert fd.read() == ""

        with client.open("sample.txt", mode="rb") as binary_fd:
            # Test various .read() variants
            assert binary_fd.read(len(text1)) == text1.encode()
            assert binary_fd.read() == text1.encode()
            assert binary_fd.read() == b""

        with fs.open("sample.txt", mode="r") as fd:
            # Test various .read() variants
            assert fd.read(len(text1)) == text1
            assert fd.read() == text1
            assert fd.read() == ""

        with fs.open("sample.txt") as fd:
            # Test various .read() variants
            assert fd.read(len(text1)) == text1.encode()
            assert fd.read() == text1.encode()
            assert fd.read() == b""

        # when we cannot detect support for ranges, we should just raise error
        client.detected_features.supports_ranges = False
        with client.open("sample.txt", mode="rb") as binary_fd:
            binary_fd._initial_response.headers.clear()  # type: ignore
            with pytest.raises(HTTPNetworkError):
                binary_fd.read()

            assert binary_fd.supports_ranges is False  # type: ignore
            with pytest.raises(ValueError) as exc_info:
                binary_fd.seek(10)
            assert str(exc_info.value) == "server does not support ranges"

        with fs.open("sample.txt", mode="rb") as fd:
            fd.reader._initial_response.headers.clear()  # type: ignore
            with pytest.raises(HTTPNetworkError):
                fd.read()

            assert fd.reader.supports_ranges is False  # type: ignore
            with pytest.raises(ValueError) as exc_info:
                fd.seek(10)
            assert str(exc_info.value) == "server does not support ranges"


def test_resume_rejects_response_that_ignores_range(
    storage_dir: TmpDir,
    client: Client,
    monkeypatch: MonkeyPatch,
):
    """A resume response that isn't a real 206 continuation must be rejected.

    Splicing it into the output regardless (the old behaviour) would
    silently corrupt the downloaded file.
    """
    from webdav import stream as stream_module

    original_iter_bytes = HTTPResponse.iter_bytes

    def bad_iter_content(
        response: "HTTPResponse", *args: Any, **kwargs: Any,
    ) -> Iterator[bytes]:
        it = original_iter_bytes(response, *args, **kwargs)
        for i, chunk in enumerate(it):
            if i > 0:
                raise HTTPNetworkError(
                    "Simulated connection drop", request=response.request,
                )
            yield chunk

    text = "0123456789" * (client.chunk_size // 10 + 1)
    storage_dir.gen("sample.txt", text * 2)

    monkeypatch.setattr(HTTPResponse, "iter_bytes", bad_iter_content)

    original_request = stream_module.request

    def fake_request(http_client: Any, url: Any, pos: int = 0) -> "HTTPResponse":
        if pos:
            import httpx

            return httpx.Response(
                200,
                content=b"unexpected full content instead of a resume",
                request=httpx.Request("GET", str(url)),
            )
        return original_request(http_client, url, pos=pos)

    monkeypatch.setattr(stream_module, "request", fake_request)

    with pytest.raises(HTTPNetworkError):
        with client.open("sample.txt", mode="rb") as fd:
            fd.read()


def test_resume_retry_is_bounded(
    storage_dir: TmpDir,
    client: Client,
    monkeypatch: MonkeyPatch,
):
    """Repeated connection drops during resume must not retry forever.

    Only the streaming GET's response is made to fail - patching
    ``iter_bytes`` on the class would also break the PROPFIND response
    that ``client.open()`` reads internally before streaming even starts.
    """
    from webdav import stream as stream_module

    def always_broken_iter_content(
        *args: Any, **kwargs: Any,
    ) -> Iterator[bytes]:
        raise HTTPNetworkError("Simulated connection drop")
        yield  # pragma: no cover

    text = "0123456789" * (client.chunk_size // 10 + 1)
    storage_dir.gen("sample.txt", text * 2)

    monkeypatch.setattr(stream_module.time, "sleep", lambda _: None)

    attempts = {"count": 0}
    original_request = stream_module.request

    def counting_request(http_client: Any, url: Any, pos: int = 0) -> "HTTPResponse":
        attempts["count"] += 1
        response = original_request(http_client, url, pos=pos)
        response.iter_bytes = always_broken_iter_content  # type: ignore[method-assign]
        return response

    monkeypatch.setattr(stream_module, "request", counting_request)

    with pytest.raises(HTTPNetworkError):
        with client.open("sample.txt", mode="rb") as fd:
            fd.read()

    assert attempts["count"] == stream_module.MAX_RESUME_ATTEMPTS + 1


def test_open(storage_dir: TmpDir, fs: WebdavFileSystem):
    """Test opening a remote file from webdav using fs in text mode."""
    text1 = "0123456789" * (DEFAULT_BUFFER_SIZE // 10 + 1)
    storage_dir.gen("sample.txt", text1 * 2)

    with fs.open("sample.txt", mode="r") as f:
        assert not f.closed
        assert not f.isatty()
        assert f.readable()
        assert not f.writable()
        assert f.seekable()
        assert f.tell() == 0
        assert f.read(len(text1)) == text1
        assert f.tell() == len(text1)
        assert f.seek(10) == 10
        assert f.read(len(text1) - 10) == text1[10:]
        assert f.tell() == len(text1)
        assert f.seek(len(text1) * 2) == len(text1) * 2
        assert f.read() == ""
        assert f.seek(0) == 0
    assert f.closed
    f.close()

    text2 = "0123456789\n"
    storage_dir.gen("sample2.txt", text2 * 10)

    with fs.open("sample2.txt", mode="r") as f:
        assert f.readline() == text2
        assert f.readlines() == [text2] * 9
    assert f.closed
    f.close()


def test_open_binary(storage_dir: TmpDir, fs: WebdavFileSystem):
    """Test file object in binary mode with fs."""
    text1 = b"0123456789" * (DEFAULT_BUFFER_SIZE // 10 + 1)
    storage_dir.gen("sample.txt", text1 * 2)
    with fs.open("sample.txt", mode="rb") as f:
        assert not f.closed
        assert not f.isatty()
        assert f.readable()
        assert not f.writable()
        assert f.seekable()
        assert f.tell() == 0

        assert f.read(len(text1)) == text1
        assert f.tell() == len(text1)

        assert f.seek(10) == 10
        assert f.read(len(text1) - 10) == text1[10:]
        assert f.tell() == len(text1)

        assert f.seek(len(text1), 1) == len(text1) * 2
        assert f.read() == b""
        assert f.seek(-len(text1), 1) == len(text1)

        assert f.seek(0) == 0
        buff = bytearray(5)
        assert f.readinto(buff) == 5
        assert bytes(buff) == text1[:5]
        length = f.readinto1(buff)
        assert bytes(buff) == text1[5 : 5 + length]
        assert f.seek(-10, 2) == f.tell() == len(text1) * 2 - 10
        assert f.read() == text1[-10:]
    assert f.closed
    f.close()

    with fs.open("sample.txt", mode="rb") as f:
        with pytest.raises(ValueError):
            f.seek(-10)
        with pytest.raises(ValueError):
            f.seek(10, 3)
    assert f.closed
    f.close()

    text2 = b"0123456789\n"
    storage_dir.gen("sample2.txt", text2 * 10)

    with fs.open("sample2.txt", mode="rb") as f:
        assert f.readline() == text2
        assert f.readlines() == [text2] * 9
    assert f.closed
    f.close()


def test_close_connection_if_nothing_is_read(client: Client):
    """Test that http connection is closed when nothing is read."""
    response = MagicMock()

    with patch.object(client.http, "send", return_value=response), patch.object(
        client, "isdir", return_value=False,
    ):
        with client.open("sample.txt", mode="rb"):
            response.close.assert_not_called()
        response.close.assert_called_once()
