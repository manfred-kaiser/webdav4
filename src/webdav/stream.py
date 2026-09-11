"""Handle streaming response for file."""

import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from functools import partial
from http import HTTPStatus
from io import RawIOBase
from typing import (
    TYPE_CHECKING,
)

from .http import HTTPNetworkError, HTTPTimeoutException
from .http import Method as HTTPMethod

if TYPE_CHECKING:
    from typing import Self

    from typing_extensions import Buffer

    from .client import Client
    from .http import Client as HTTPClient
    from .types import HTTPResponse, URLTypes

MAX_RESUME_ATTEMPTS = 5
RESUME_BACKOFF_SECONDS = 1.0


def _validate_resumed_response(
    response: "HTTPResponse",
    pos: int,
    etag: str | None,
    last_modified: str | None,
) -> None:
    """Ensures a ranged response actually continues from ``pos``.

    A server that silently ignores the ``Range`` header (returning ``200``
    with the full body instead of ``206`` starting at ``pos``), or that
    serves a different representation of the resource than the one the
    stream started with, would otherwise get spliced into the output with
    no indication that anything went wrong.
    """
    if response.status_code != HTTPStatus.PARTIAL_CONTENT:
        raise HTTPNetworkError(
            f"expected a 206 Partial Content response resuming at byte "
            f"{pos}, got {response.status_code}",
        )

    content_range = response.headers.get("Content-Range", "")
    if not content_range.startswith(f"bytes {pos}-"):
        raise HTTPNetworkError(
            f"expected Content-Range starting at byte {pos}, "
            f"got {content_range!r}",
        )

    new_etag = response.headers.get("ETag")
    if etag and new_etag and new_etag != etag:
        raise HTTPNetworkError("resource changed during download (ETag mismatch)")

    new_last_modified = response.headers.get("Last-Modified")
    if last_modified and new_last_modified and new_last_modified != last_modified:
        raise HTTPNetworkError(
            "resource changed during download (Last-Modified mismatch)",
        )


def request(client: "HTTPClient", url: "URLTypes", pos: int = 0) -> "HTTPResponse":
    """Streams a file from url from given position."""
    headers = {}
    if pos:
        headers.update({"Range": f"bytes={pos}-"})

    req = client.build_request(HTTPMethod.GET, url, headers=headers)
    return client.send(req, stream=True, follow_redirects=True)


@contextmanager
def iter_url(
    client: "Client",
    url: "URLTypes",
    chunk_size: int | None = None,
    pos: int = 0,
) -> Iterator[tuple["HTTPResponse", Iterator[bytes]]]:
    """Iterate over chunks requested from url.

    Reopens connection on network failure.
    """

    def gen(
        response: "HTTPResponse",
    ) -> Generator[bytes, None, None]:
        nonlocal pos
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        attempts = 0
        try:
            while True:
                if response.status_code == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
                    return  # range request outside file
                response.raise_for_status()

                if pos:
                    _validate_resumed_response(response, pos, etag, last_modified)

                try:
                    for chunk in response.iter_bytes(chunk_size=chunk_size):
                        pos += len(chunk)
                        yield chunk
                    break
                except (HTTPTimeoutException, HTTPNetworkError):
                    response.close()
                    if not (
                        response.headers.get("Accept-Ranges") == "bytes"
                        or client.detected_features.supports_ranges
                    ):
                        raise
                    attempts += 1
                    if attempts > MAX_RESUME_ATTEMPTS:
                        raise
                    time.sleep(RESUME_BACKOFF_SECONDS * attempts)
                    response = request(client.http, url, pos=pos)
        finally:
            response.close()

    response = request(client.http, url, pos=pos)
    chunks = gen(response)
    try:
        yield response, chunks
    finally:
        # Ensure connection is closed
        chunks.close()
        response.close()


class IterStream(RawIOBase):
    """Create a streaming file-like object."""

    def __init__(
        self,
        client: "Client",
        url: "URLTypes",
        chunk_size: int | None = None,
    ) -> None:
        """Pass a iterator to stream through."""
        super().__init__()

        self.buffer = b""
        # setting chunk_size is not possible yet with httpx
        # though it is to be released in a new version.
        self.chunk_size = chunk_size or client.chunk_size
        self.client = client
        self.url = url
        self._loc: int = 0
        self._cm = iter_url(client, self.url, chunk_size=chunk_size)
        self._iterator: Iterator[bytes] | None = None
        self._initial_response: HTTPResponse | None = None

    @property
    def supports_ranges(self) -> bool:
        """Checks whether the server supports ranges for the resource.

        Even if it does not advertise, we'll use base url's OPTIONS request
        to see if the server supports Range header or not. And, we want to
        avoid checking that as much as possible.
        """
        response = self._initial_response
        if response and response.headers.get("Accept-Ranges") == "bytes":
            return True
        # consider if checking Accept-Ranges from OPTIONS request on self.url
        # would be a better solution than using base url.
        return self.client.detected_features.supports_ranges

    @property
    def size(self) -> int | None:
        """Size of the file object."""
        assert self._initial_response
        content_length: str = self._initial_response.headers.get("Content-Length", "")
        return int(content_length) if content_length.isdigit() else None

    @property
    def loc(self) -> int:
        """Keep track of location of the stream/file for callbacks."""
        return self._loc

    @loc.setter
    def loc(self, value: int) -> None:
        """Update location, and run callbacks."""
        self._loc = value

    def __enter__(self) -> "Self":
        """Send a streaming response."""
        self._initial_response, self._iterator = self._cm.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        """Close the response."""
        self.close()

    @property
    def encoding(self) -> str | None:
        """Encoding of the response."""
        assert self._initial_response
        return self._initial_response.encoding

    def close(self) -> None:
        """Close response if not already."""
        if self._iterator:
            self._cm.__exit__(None, None, None)

        self._iterator = None
        self.buffer = b""

    def seek(self, offset: int, whence: int = 0) -> int:
        """Seek the file object."""
        if whence == 0:
            loc = offset
        elif whence == 1:
            if offset >= 0:
                self.read(offset)
                return self.loc
            loc = self.loc + offset
        elif whence == 2:
            if not self.size:
                raise ValueError("cannot seek to the end of file")
            loc = self.size + offset
        else:
            raise ValueError(f"invalid whence ({whence}, should be 0, 1 or 2)")
        if loc < 0:
            raise ValueError("Seek before start of file")
        if loc and not self.supports_ranges:
            raise ValueError("server does not support ranges")

        self.close()
        self._cm = iter_url(self.client, self.url, pos=loc, chunk_size=self.chunk_size)
        _, self._iterator = self._cm.__enter__()
        self.loc = loc
        return loc

    def tell(self) -> int:
        """Return current position of the fileobj."""
        return self.loc

    @property
    def closed(self) -> bool:
        """Check whether the stream was closed or not."""
        return self._iterator is None

    def readable(self) -> bool:
        """Stream is readable."""
        return True

    def seekable(self) -> bool:
        """Stream is not seekable."""
        return True

    def writable(self) -> bool:
        """Stream not writable."""
        return False

    def readall(self) -> bytes:
        """Read all of the bytes."""
        return b"".join(iter(partial(self.read1, -1), b""))

    def read(self, num: int = -1) -> bytes:
        """Read n bytes at max."""
        if num < 0:
            return self.readall()

        buff = b""
        while len(buff) < num:
            chunk = self.read1(num - len(buff))
            if not chunk:
                break
            buff += chunk
        return buff

    def read1(self, num: int = -1) -> bytes:
        """Read at maximum once."""
        assert self._iterator
        try:
            chunk = self.buffer or next(self._iterator)
        except StopIteration:
            return b""

        if num <= 0:
            output, self.buffer = chunk, b""
        else:
            output, self.buffer = chunk[:num], chunk[num:]

        self.loc += len(output)
        return output

    def readinto(self, sequence: "Buffer") -> int:
        """Read into the buffer."""
        out = memoryview(sequence).cast("B")
        data = self.read(out.nbytes)
        out[: len(data)] = data
        return len(data)

    def readinto1(self, sequence: "Buffer") -> int:
        """Read into the buffer with 1 read at max."""
        out = memoryview(sequence).cast("B")
        data = self.read1(out.nbytes)
        out[: len(data)] = data
        return len(data)
