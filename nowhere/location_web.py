"""Consent-first mobile upload page and HTTP API for live location."""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from nowhere.live_location import LocationError, get_store, iso_time, session_status


_STATIC_DIR = Path(__file__).resolve().parent / "static"
_MAX_BODY_BYTES = 16 * 1024


def _security_headers(response: Response, *, no_store: bool = True) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    if no_store:
        response.headers["Cache-Control"] = "no-store"
    return response


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return _security_headers(JSONResponse(payload, status_code=status))  # type: ignore[return-value]


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    prefix = "bearer "
    return header[len(prefix):].strip() if header.lower().startswith(prefix) else ""


def _error_response(error: Exception) -> JSONResponse:
    if isinstance(error, LocationError):
        status = {
            "invalid_input": 400,
            "invalid_write_token": 401,
            "session_not_found": 404,
            "session_stopped": 410,
            "session_expired": 410,
            "too_many_sessions": 429,
        }.get(error.code, 400)
        return _json({"error": {"code": error.code, "message": str(error)}}, status)
    return _json({"error": {"code": "internal_error", "message": "定位服务暂时不可用"}}, 500)


def _session_payload(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session["id"],
        "label": session["label"],
        "precision": session["precision"],
        "status": session_status(session),
        "created_at": iso_time(session["created_at"]),
        "expires_at": iso_time(session["expires_at"]),
        "retention_hours": session["retention_hours"],
        "update_interval_seconds": 10,
    }


async def location_share_page(request: Request) -> Response:
    if get_store().get_session(request.path_params["session_id"]) is None:
        return _json({"error": {"code": "session_not_found", "message": "找不到这次定位分享"}}, 404)
    response = HTMLResponse((_STATIC_DIR / "location-share.html").read_text(encoding="utf-8"))
    return _security_headers(response)


async def location_asset(request: Request) -> Response:
    name = request.path_params["name"]
    allowed = {
        "app.js": ("location-share.js", "text/javascript"),
        "styles.css": ("location-share.css", "text/css"),
    }
    target = allowed.get(name)
    if target is None:
        return _json({"error": {"code": "not_found", "message": "Not found"}}, 404)
    response = Response((_STATIC_DIR / target[0]).read_bytes(), media_type=target[1])
    response.headers["Cache-Control"] = "public, max-age=300"
    return _security_headers(response, no_store=False)


async def location_status(request: Request) -> JSONResponse:
    try:
        session = get_store().verify_write_token(request.path_params["session_id"], _bearer(request))
        return _json({"session": _session_payload(session)})
    except Exception as error:
        return _error_response(error)


class _RateLimiter:
    def __init__(self, limit: int = 120, window_seconds: int = 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._buckets: dict[str, tuple[int, float]] = {}

    def accept(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        count, reset_at = self._buckets.get(key, (0, now + self.window_seconds))
        if reset_at <= now:
            count, reset_at = 0, now + self.window_seconds
        count += 1
        self._buckets[key] = (count, reset_at)
        if len(self._buckets) > 2000:
            self._buckets = {k: v for k, v in self._buckets.items() if v[1] > now}
        return count <= self.limit


_upload_limiter = _RateLimiter()


def _parse_recorded_at(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise LocationError("invalid_input", "captured_at 格式无效")
    if isinstance(value, (int, float)) and math.isfinite(value):
        # Browser Date.now() is milliseconds; internal storage is seconds.
        return float(value) / 1000 if value > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError as exc:
            raise LocationError("invalid_input", "captured_at 格式无效") from exc
    raise LocationError("invalid_input", "captured_at 格式无效")


async def _request_json(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                raise LocationError("invalid_input", "请求内容过大")
        except ValueError as exc:
            raise LocationError("invalid_input", "Content-Length 无效") from exc
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_BODY_BYTES:
            raise LocationError("invalid_input", "请求内容过大")
        raw.extend(chunk)
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise LocationError("invalid_input", "请求必须是有效 JSON") from exc
    if not isinstance(body, dict):
        raise LocationError("invalid_input", "请求 JSON 必须是对象")
    return body


def _location_payload(body: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "latitude": body.get("latitude"),
        "longitude": body.get("longitude"),
        "accuracy_meters": body.get("accuracy_m", body.get("accuracy_meters")),
        "altitude_meters": body.get("altitude_m", body.get("altitude_meters")),
        "speed_meters_per_second": body.get("speed_mps", body.get("speed_meters_per_second")),
        "heading_degrees": body.get("heading_deg", body.get("heading_degrees")),
    }
    recorded_at = _parse_recorded_at(body.get("captured_at", body.get("recorded_at")))
    if recorded_at is not None:
        payload["recorded_at"] = recorded_at
    return payload


async def location_upload(request: Request) -> JSONResponse:
    session_id = request.path_params["session_id"]
    token = _bearer(request)
    try:
        # Authenticate before allocating a rate-limit bucket for a public ID.
        get_store().verify_write_token(session_id, token)
        if not _upload_limiter.accept(session_id):
            response = _json(
                {"error": {"code": "rate_limited", "message": "位置更新太频繁，请稍后重试"}}, 429
            )
            response.headers["Retry-After"] = "60"
            return response
        body = await _request_json(request)
        location = get_store().add_location(session_id, token, _location_payload(body))
        return _json({"accepted": True, "received_at": iso_time(location["received_at"])}, 202)
    except Exception as error:
        return _error_response(error)


async def location_stop(request: Request) -> JSONResponse:
    try:
        result = get_store().stop_with_token(
            request.path_params["session_id"], _bearer(request), erase_history=False
        )
        return _json(
            {"stopped": True, "stopped_at": iso_time(result["session"]["stopped_at"])}
        )
    except Exception as error:
        return _error_response(error)


def location_routes() -> list[Route]:
    return [
        Route("/share/{session_id:str}", location_share_page),
        Route("/location-assets/{name:str}", location_asset),
        Route("/api/location/share/{session_id:str}/status", location_status),
        Route("/api/location/share/{session_id:str}/positions", location_upload, methods=["POST"]),
        Route("/api/location/share/{session_id:str}/stop", location_stop, methods=["POST"]),
    ]


@asynccontextmanager
async def location_lifespan(_app: Starlette) -> AsyncIterator[None]:
    store = get_store()
    store.purge()
    stop = asyncio.Event()
    interval = max(30, int(os.environ.get("NOWHERE_LOCATION_SWEEP_SECONDS", "900")))

    async def sweep() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                store.purge()

    task = asyncio.create_task(sweep(), name="nowhere-location-retention")
    try:
        yield
    finally:
        stop.set()
        await task
        store.purge()


def create_location_mcp_app(mcp: Any) -> Starlette:
    """Compose `/mcp` and only the location web surface on one safe server."""

    mcp_app = mcp.http_app(path="/mcp", transport="streamable-http")

    @asynccontextmanager
    async def combined_lifespan(app: Starlette) -> AsyncIterator[None]:
        async with mcp_app.router.lifespan_context(mcp_app):
            async with location_lifespan(app):
                yield

    async def health(_request: Request) -> JSONResponse:
        return _json({"ok": True, "service": "nowhere", "location_sharing": True})

    return Starlette(
        routes=[Route("/health", health), *location_routes(), *mcp_app.routes],
        lifespan=combined_lifespan,
    )

