"""Privacy-scoped live-location sessions for travelling together.

Real GPS data deliberately lives outside :class:`WorldState`: it has a short
retention window, a separate capability token, and may be erased without
touching the fictional journey archive.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


class LocationError(Exception):
    """A safe, user-facing location-domain error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


Clock = Callable[[], float]


def _default_path() -> Path:
    home = Path(os.environ.get("NOWHERE_HOME", "~/.nowhere")).expanduser()
    return home / "live_location.sqlite3"


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def iso_time(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def session_status(session: dict[str, Any], now: float | None = None) -> str:
    now = time.time() if now is None else now
    if session.get("stopped_at") is not None:
        return "stopped"
    if float(session["expires_at"]) <= now:
        return "expired"
    return "active"


class LocationStore:
    """Small synchronous SQLite store safe for MCP and Starlette threads."""

    def __init__(self, path: str | Path | None = None, clock: Clock = time.time):
        self.path = Path(path) if path is not None else _default_path()
        self.clock = clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            # SQLite's context manager commits/rolls back, but does not close.
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS share_sessions (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    precision TEXT NOT NULL CHECK (precision IN ('exact', 'approximate')),
                    write_token_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    stopped_at REAL,
                    retention_hours INTEGER NOT NULL CHECK (retention_hours BETWEEN 1 AND 168)
                );
                CREATE INDEX IF NOT EXISTS share_sessions_active_idx
                    ON share_sessions(stopped_at, expires_at, created_at DESC);
                CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES share_sessions(id) ON DELETE CASCADE,
                    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
                    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
                    accuracy_meters REAL,
                    altitude_meters REAL,
                    speed_meters_per_second REAL,
                    heading_degrees REAL,
                    recorded_at REAL NOT NULL,
                    received_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS locations_session_time_idx
                    ON locations(session_id, recorded_at DESC, id DESC);
                PRAGMA user_version = 1;
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _session(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row["id"],
            "label": row["label"],
            "precision": row["precision"],
            "created_at": float(row["created_at"]),
            "expires_at": float(row["expires_at"]),
            "stopped_at": None if row["stopped_at"] is None else float(row["stopped_at"]),
            "retention_hours": int(row["retention_hours"]),
        }

    @staticmethod
    def _location(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "session_id": row["session_id"],
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "accuracy_meters": row["accuracy_meters"],
            "altitude_meters": row["altitude_meters"],
            "speed_meters_per_second": row["speed_meters_per_second"],
            "heading_degrees": row["heading_degrees"],
            "recorded_at": float(row["recorded_at"]),
            "received_at": float(row["received_at"]),
        }

    def create_session(
        self,
        *,
        label: str = "一起旅行",
        duration_minutes: int = 240,
        precision: str = "exact",
        retention_hours: int = 24,
    ) -> tuple[dict[str, Any], str]:
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int) or not 5 <= duration_minutes <= 10_080:
            raise LocationError("invalid_input", "duration_minutes 必须是 5 到 10080 的整数")
        if isinstance(retention_hours, bool) or not isinstance(retention_hours, int) or not 1 <= retention_hours <= 168:
            raise LocationError("invalid_input", "retention_hours 必须是 1 到 168 的整数")
        if precision not in {"exact", "approximate"}:
            raise LocationError("invalid_input", "precision 只能是 exact 或 approximate")
        label = label.strip()
        if not 1 <= len(label) <= 80:
            raise LocationError("invalid_input", "label 必须是 1 到 80 个字符")

        now = self.clock()
        self.purge(now)
        with self._lock, self._connect() as db:
            active_count = db.execute(
                "SELECT COUNT(*) FROM share_sessions WHERE stopped_at IS NULL AND expires_at > ?",
                (now,),
            ).fetchone()[0]
            if active_count >= 20:
                raise LocationError("too_many_sessions", "有效定位分享已达到 20 个，请先结束旧会话")
            session_id = str(uuid.uuid4())
            write_token = secrets.token_urlsafe(32)
            db.execute(
                """INSERT INTO share_sessions
                   (id, label, precision, write_token_hash, created_at, expires_at, stopped_at, retention_hours)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    session_id,
                    label,
                    precision,
                    _token_digest(write_token),
                    now,
                    now + duration_minutes * 60,
                    retention_hours,
                ),
            )
        session = self.get_session(session_id)
        assert session is not None
        return session, write_token

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM share_sessions WHERE id = ?", (session_id,)).fetchone()
        return self._session(row)

    def newest_session(self, *, active_only: bool = False) -> dict[str, Any] | None:
        now = self.clock()
        query = "SELECT * FROM share_sessions"
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE stopped_at IS NULL AND expires_at > ?"
            params = (now,)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._lock, self._connect() as db:
            row = db.execute(query, params).fetchone()
        return self._session(row)

    def verify_write_token(self, session_id: str, token: str) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM share_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            expected = row["write_token_hash"] if row is not None else "0" * 64
        if row is None:
            raise LocationError("session_not_found", "找不到这次定位分享")
        if not hmac.compare_digest(str(expected), _token_digest(token)):
            raise LocationError("invalid_write_token", "分享凭证无效")
        session = self._session(row)
        assert session is not None
        return session

    @staticmethod
    def _finite(value: Any, name: str, minimum: float, maximum: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise LocationError("invalid_input", f"{name} 必须是有限数字")
        result = float(value)
        if not minimum <= result <= maximum:
            raise LocationError("invalid_input", f"{name} 必须在 {minimum} 到 {maximum} 之间")
        return result

    @staticmethod
    def _optional(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise LocationError("invalid_input", f"{name} 必须是有限数字")
        result = float(value)
        if minimum is not None and result < minimum:
            raise LocationError("invalid_input", f"{name} 不能小于 {minimum}")
        if maximum is not None and result > maximum:
            raise LocationError("invalid_input", f"{name} 不能大于 {maximum}")
        return result

    def add_location(self, session_id: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        session = self.verify_write_token(session_id, token)
        status = session_status(session, now)
        if status == "stopped":
            raise LocationError("session_stopped", "这次定位分享已经结束")
        if status == "expired":
            raise LocationError("session_expired", "这次定位分享已经过期")

        latitude = self._finite(payload.get("latitude"), "latitude", -90, 90)
        longitude = self._finite(payload.get("longitude"), "longitude", -180, 180)
        if session["precision"] == "approximate":
            latitude = round(latitude, 2)
            longitude = round(longitude, 2)
        recorded_at = payload.get("recorded_at", now)
        recorded_at = self._finite(recorded_at, "recorded_at", 0, now + 300)
        accuracy = self._optional(payload.get("accuracy_meters"), "accuracy_meters", minimum=0)
        altitude = self._optional(payload.get("altitude_meters"), "altitude_meters")
        speed = self._optional(payload.get("speed_meters_per_second"), "speed_meters_per_second", minimum=0)
        heading = self._optional(payload.get("heading_degrees"), "heading_degrees", minimum=0, maximum=360)

        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT stopped_at, expires_at FROM share_sessions WHERE id = ?", (session_id,)).fetchone()
            if current is None or current["stopped_at"] is not None or current["expires_at"] <= self.clock():
                raise LocationError("session_expired", "定位分享已经停止或过期")
            cursor = db.execute(
                """INSERT INTO locations
                   (session_id, latitude, longitude, accuracy_meters, altitude_meters,
                    speed_meters_per_second, heading_degrees, recorded_at, received_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, latitude, longitude, accuracy, altitude, speed, heading, recorded_at, now),
            )
            row = db.execute("SELECT * FROM locations WHERE id = ?", (cursor.lastrowid,)).fetchone()
        result = self._location(row)
        assert result is not None
        return result

    def latest_location(self, session_id: str) -> dict[str, Any] | None:
        if self.get_session(session_id) is None:
            raise LocationError("session_not_found", "找不到这次定位分享")
        self.purge()
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM locations WHERE session_id = ? ORDER BY recorded_at DESC, id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return self._location(row)

    def timeline(self, session_id: str, *, minutes: int = 60, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 10_080:
            raise LocationError("invalid_input", "minutes 必须是 1 到 10080 的整数")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise LocationError("invalid_input", "limit 必须是 1 到 200 的整数")
        if self.get_session(session_id) is None:
            raise LocationError("session_not_found", "找不到这次定位分享")
        now = self.clock()
        self.purge(now)
        with self._lock, self._connect() as db:
            rows = db.execute(
                """SELECT * FROM locations WHERE session_id = ? AND recorded_at >= ?
                   ORDER BY recorded_at DESC, id DESC LIMIT ?""",
                (session_id, now - minutes * 60, limit),
            ).fetchall()
        return [location for row in rows if (location := self._location(row)) is not None]

    def stop_session(self, session_id: str, *, erase_history: bool = False) -> dict[str, Any]:
        if self.get_session(session_id) is None:
            raise LocationError("session_not_found", "找不到这次定位分享")
        now = self.clock()
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE share_sessions SET stopped_at = COALESCE(stopped_at, ?) WHERE id = ?",
                (now, session_id),
            )
            erased = 0
            if erase_history:
                erased = db.execute("DELETE FROM locations WHERE session_id = ?", (session_id,)).rowcount
            row = db.execute("SELECT * FROM share_sessions WHERE id = ?", (session_id,)).fetchone()
        session = self._session(row)
        assert session is not None
        return {"session": session, "erased_location_count": max(0, erased)}

    def stop_with_token(self, session_id: str, token: str, *, erase_history: bool = False) -> dict[str, Any]:
        self.verify_write_token(session_id, token)
        return self.stop_session(session_id, erase_history=erase_history)

    def purge(self, now: float | None = None) -> dict[str, int]:
        now = self.clock() if now is None else now
        with self._lock, self._connect() as db:
            locations = db.execute(
                """DELETE FROM locations
                   WHERE received_at < (
                       SELECT ? - share_sessions.retention_hours * 3600
                       FROM share_sessions WHERE share_sessions.id = locations.session_id
                   )""",
                (now,),
            ).rowcount
            sessions = db.execute(
                """DELETE FROM share_sessions
                   WHERE NOT EXISTS (
                       SELECT 1 FROM locations WHERE locations.session_id = share_sessions.id
                   )
                   AND COALESCE(stopped_at, expires_at) < (? - retention_hours * 3600)""",
                (now,),
            ).rowcount
        return {"locations": max(0, locations), "sessions": max(0, sessions)}


_store: LocationStore | None = None
_store_path: Path | None = None
_store_lock = threading.Lock()


def get_store() -> LocationStore:
    """Return the lazy store, respecting per-test/per-process NOWHERE_HOME."""

    global _store, _store_path
    path = _default_path()
    with _store_lock:
        if _store is None or _store_path != path:
            _store = LocationStore(path)
            _store_path = path
        return _store


def reset_store_for_tests() -> None:
    global _store, _store_path
    with _store_lock:
        _store = None
        _store_path = None

