from __future__ import annotations

import json
import sqlite3

import pytest
from starlette.requests import Request

from nowhere import live_location, location_web


@pytest.fixture
def connections(monkeypatch):
    opened = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(live_location.sqlite3, "connect", connect)
    yield opened
    # Clean up even if the regression is reproduced against the old version.
    for connection in opened:
        connection.close()


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_every_store_operation_closes_its_connection(tmp_path, connections):
    store = live_location.LocationStore(tmp_path / "location.sqlite3")
    session, token = store.create_session()
    store.add_location(session["id"], token, {"latitude": 30, "longitude": 120})
    store.get_session(session["id"])
    store.newest_session(active_only=True)
    store.verify_write_token(session["id"], token)
    store.latest_location(session["id"])
    store.timeline(session["id"])
    store.stop_with_token(session["id"], token, erase_history=True)
    store.purge()
    assert connections
    for connection in connections:
        assert_closed(connection)


def test_failed_transaction_rolls_back_and_closes(tmp_path, connections):
    store = live_location.LocationStore(tmp_path / "location.sqlite3")
    session, _ = store.create_session(label="original")
    with pytest.raises(RuntimeError, match="interrupted"):
        with store._connect() as db:
            db.execute("UPDATE share_sessions SET label = 'changed' WHERE id = ?", (session["id"],))
            raise RuntimeError("interrupted")
    assert_closed(db)
    assert store.get_session(session["id"])["label"] == "original"


def test_connection_is_closed_when_configuration_fails(tmp_path, monkeypatch):
    opened = []
    original_connect = sqlite3.connect

    class BrokenConfiguration(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA foreign_keys = ON":
                raise sqlite3.OperationalError("configuration failed")
            return super().execute(sql, *args, **kwargs)

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=BrokenConfiguration)
        opened.append(connection)
        return connection

    monkeypatch.setattr(live_location.sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError, match="configuration failed"):
            live_location.LocationStore(tmp_path / "location.sqlite3")
        assert len(opened) == 1
        assert_closed(opened[0])
    finally:
        for connection in opened:
            connection.close()


def streamed_request(chunks, content_length=None):
    received = []
    chunks = iter(chunks)

    async def receive():
        chunk, more = next(chunks)
        received.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": more}

    headers = [] if content_length is None else [(b"content-length", content_length.encode())]
    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    return Request(scope, receive), received


@pytest.mark.parametrize("content_length", [None, "1"])
async def test_oversized_stream_stops_without_consuming_the_rest(content_length):
    request, received = streamed_request(
        [(b"x" * (location_web._MAX_BODY_BYTES + 1), True), (b"tail", False)], content_length
    )
    with pytest.raises(live_location.LocationError, match="请求内容过大"):
        await location_web._request_json(request)
    assert len(received) == 1


async def test_byte_limit_accumulates_across_chunks():
    request, received = streamed_request(
        [(b" " * location_web._MAX_BODY_BYTES, True), (b"x", True), (b"tail", False)]
    )
    with pytest.raises(live_location.LocationError, match="请求内容过大"):
        await location_web._request_json(request)
    assert len(received) == 2


async def test_exact_byte_limit_and_split_utf8_still_parse():
    encoded = json.dumps({"label": "测试"}, ensure_ascii=False).encode()
    encoded += b" " * (location_web._MAX_BODY_BYTES - len(encoded))
    split = encoded.index("测".encode()) + 1
    request, _ = streamed_request([(encoded[:split], True), (encoded[split:], False)])
    assert await location_web._request_json(request) == {"label": "测试"}


@pytest.mark.parametrize("body", [b"{", b"[]", b"null", b"\xff"])
async def test_invalid_streams_keep_domain_error(body):
    request, _ = streamed_request([(body, False)])
    with pytest.raises(live_location.LocationError) as caught:
        await location_web._request_json(request)
    assert caught.value.code == "invalid_input"
