"""Shared fake aiohttp session, response and stream content for HTTP tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from multidict import CIMultiDict
from yarl import URL


class FakeContent:
    """Yield fixed chunks, optionally re-split by the reader's limit, then raise."""

    def __init__(
        self,
        chunks: bytes | list[bytes | str] | None = None,
        *,
        error: BaseException | None = None,
        split: bool = False,
    ) -> None:
        self._chunks = [chunks] if isinstance(chunks, bytes) else list(chunks or [])
        self._error = error
        self._split = split
        self.yielded_chunks = 0

    async def iter_chunked(self, limit: int) -> AsyncIterator[bytes | str]:
        for chunk in self._chunks:
            if self._split and isinstance(chunk, bytes):
                parts = [chunk[i : i + limit] for i in range(0, len(chunk), limit)]
            else:
                parts = [chunk]
            for part in parts:
                self.yielded_chunks += 1
                yield part
        if self._error is not None:
            raise self._error


@dataclass
class FakeResponse:
    """One response; ``body`` is a single chunk or a list of chunks."""

    status: int = 200
    body: bytes | list[bytes | str] = b""
    url: URL | None = None
    headers: CIMultiDict[str] = field(default_factory=CIMultiDict, kw_only=True)
    history: tuple[Any, ...] = field(default=(), kw_only=True)
    declared_length: int | None = field(default=None, kw_only=True)
    content: Any = field(default=None, kw_only=True)
    split: bool = field(default=False, kw_only=True)
    connection: Any = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.content is None:
            self.content = FakeContent(self.body, split=self.split)

    @property
    def content_length(self) -> int | None:
        return self.declared_length

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeSession:
    """Serve one response, a queue (list, popped in order) or a str-URL map.

    ``requests`` logs ``(url, kwargs)`` per GET or POST; a map logs ``str(url)``,
    the other modes log the URL object exactly as the caller passed it.
    """

    def __init__(
        self,
        responses: FakeResponse
        | list[FakeResponse]
        | dict[str, FakeResponse]
        | None = None,
        *,
        error: BaseException | None = None,
        allow_post: bool = True,
    ) -> None:
        self.allow_post = allow_post
        self.responses: Any = [] if responses is None else responses
        self.error = error
        self.requests: list[tuple[Any, dict[str, Any]]] = []

    def get(self, url: Any, **kwargs: Any) -> FakeResponse:
        by_url = isinstance(self.responses, dict)
        self.requests.append((str(url) if by_url else url, kwargs))
        if self.error is not None:
            raise self.error
        if by_url:
            return self.responses[str(url)]
        if isinstance(self.responses, FakeResponse):
            return self.responses
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)

    def post(self, url: Any, **kwargs: Any) -> FakeResponse:
        if not self.allow_post:
            raise AssertionError("unexpected POST")
        return self.get(url, **kwargs)
