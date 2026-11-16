"""Bounded streaming and ownership at the shared single-request seam."""

import httpx
import pytest

from stackos_connectors import CallOptions
from stackos_connectors.contracts import ConnectorRequest
from stackos_connectors.errors import ConnectorError
from stackos_connectors.shared.http import request_json


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    async def aclose(self):
        self.closed = True


async def call(http, limit):
    request = ConnectorRequest(
        connector="fixture",
        action_key="fixture.write",
        operation="operation.execute",
        input_json={},
        config_json={},
        options=CallOptions(http=http),
    )
    return await request_json(
        request,
        vendor="fixture",
        method="POST",
        url="https://example.test/api/fixture",
        headers={},
        params=[],
        max_response_bytes=limit,
    )


async def test_stream_at_limit_and_owned_response_cleanup():
    stream = ChunkStream([b'{"ok":', b"true}"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)),
        timeout=17,
        follow_redirects=True,
    ) as http:
        status, body, _ = await call(http, 11)
        assert status == 200 and body == {"ok": True}
        assert stream.closed and not http.is_closed
        assert http.timeout.read == 17 and http.follow_redirects


async def test_oversize_stops_reading_closes_response_and_never_replays():
    stream = ChunkStream([b"12345678", b"overflow", b"never-read"])
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ConnectorError, match="byte limit") as failed:
            await call(http, 8)
        assert not http.is_closed
    assert len(calls) == 1 and stream.reads == 2 and stream.closed
    assert failed.value.metadata_json["provider_executed"] is True
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False


async def test_late_stream_failure_remains_unknown_and_closes_response():
    stream = ChunkStream([b'{"ok":', httpx.ReadError("interrupted")])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as http:
        with pytest.raises(ConnectorError) as failed:
            await call(http, 100)
        assert not http.is_closed
    assert stream.closed and stream.reads == 2
    assert failed.value.metadata_json["outcome_unknown"] is True
    assert failed.value.metadata_json["retry_safe"] is False
