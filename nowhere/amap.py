"""Server-side AMap Web Service adapter with explicit coordinate semantics."""

from __future__ import annotations

import math
import os
from typing import Any
from urllib.parse import urlencode, urlparse

from nowhere import country, providers


OVERSEAS_WARNING = (
    "高德 Web Service 对中国大陆以外（包括济州岛）的逆地理、POI 与路线覆盖可能不完整；"
    "坐标仍保持 WGS-84 原值，失败时请直接使用坐标。"
)


class AmapError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def is_mainland_china(latitude: float, longitude: float) -> bool:
    """Return whether the standard GCJ-02 transform applies.

    The public transform convention uses this broad mainland envelope.  It is
    intentionally false for Jeju (33N, 126E), Hong Kong-adjacent overseas
    points, and all other foreign travel.
    """

    if not (0.8293 <= latitude <= 55.8271 and 72.004 <= longitude <= 137.8347):
        return False
    # The bounding box alone incorrectly includes Jeju, Korea, Japan and much
    # of Southeast Asia.  Nowhere already ships an offline Natural Earth grid,
    # so use its ISO result to keep overseas coordinates strictly WGS-84.
    return country.country_code_of(latitude, longitude) == "CN"


def _transform_lat(x: float, y: float) -> float:
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    value += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return value


def _transform_lon(x: float, y: float) -> float:
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    value += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return value


def wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    if not is_mainland_china(latitude, longitude):
        return latitude, longitude
    a = 6378245.0
    ee = 0.00669342162296594323
    dlat = _transform_lat(longitude - 105.0, latitude - 35.0)
    dlon = _transform_lon(longitude - 105.0, latitude - 35.0)
    radlat = latitude / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (a / sqrt_magic * math.cos(radlat) * math.pi)
    return latitude + dlat, longitude + dlon


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        return next((item.strip() for item in value if isinstance(item, str) and item.strip()), None)
    return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _coordinate(value: Any) -> dict[str, float] | None:
    raw = _text(value)
    if raw is None:
        return None
    try:
        longitude, latitude = (float(part) for part in raw.split(",", 1))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    return {"latitude": latitude, "longitude": longitude}


class AmapClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = (api_key if api_key is not None else (
            os.environ.get("NOWHERE_AMAP_KEY") or os.environ.get("AMAP_API_KEY")
        ) or "").strip()
        self.base_url = (base_url or os.environ.get("NOWHERE_AMAP_BASE_URL") or "https://restapi.amap.com").rstrip("/")
        parsed = urlparse(self.base_url)
        loopback = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AmapError("amap_invalid_config", "NOWHERE_AMAP_BASE_URL 必须是有效的 HTTP(S) 地址")
        if parsed.scheme != "https" and not loopback:
            raise AmapError("amap_invalid_config", "远程高德服务地址必须使用 HTTPS")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise AmapError(
                "amap_not_configured",
                "未配置 NOWHERE_AMAP_KEY；定位分享仍可用，但高德地址、附近和路线功能暂不可用。",
            )
        payload = await providers.fetch_json(
            f"{self.base_url}{path}",
            source="amap",
            cache_ttl=0,
            timeout=8.0,
            params={"key": self.api_key, **params},
        )
        if payload is None:
            raise AmapError("amap_network_error", "高德服务暂时没有响应")
        if str(payload.get("status")) != "1":
            info = _text(payload.get("info")) or "请求未成功"
            raise AmapError("amap_api_error", f"高德服务拒绝了请求：{info}")
        return payload

    @staticmethod
    def warning(latitude: float, longitude: float) -> str | None:
        return None if is_mainland_china(latitude, longitude) else OVERSEAS_WARNING

    async def reverse_geocode(self, latitude: float, longitude: float, radius_meters: int = 500) -> dict[str, Any]:
        amap_lat, amap_lon = wgs84_to_gcj02(latitude, longitude)
        payload = await self._request(
            "/v3/geocode/regeo",
            {
                "location": f"{amap_lon},{amap_lat}",
                "radius": max(0, min(3000, int(radius_meters))),
                "extensions": "base",
            },
        )
        regeocode = payload.get("regeocode")
        if not isinstance(regeocode, dict) or _text(regeocode.get("formatted_address")) is None:
            raise AmapError("amap_no_result", f"高德没有返回该坐标的地址。{self.warning(latitude, longitude) or ''}".strip())
        component = regeocode.get("addressComponent")
        component = component if isinstance(component, dict) else {}
        return {
            "formatted_address": _text(regeocode.get("formatted_address")),
            "country": _text(component.get("country")),
            "province": _text(component.get("province")),
            "city": _text(component.get("city")),
            "district": _text(component.get("district")),
            "township": _text(component.get("township")),
            "adcode": _text(component.get("adcode")),
            "amap_coordinate": {"latitude": amap_lat, "longitude": amap_lon},
            "coverage_warning": self.warning(latitude, longitude),
        }

    async def nearby(
        self,
        latitude: float,
        longitude: float,
        *,
        keyword: str = "",
        radius_meters: int = 2000,
        limit: int = 10,
    ) -> dict[str, Any]:
        amap_lat, amap_lon = wgs84_to_gcj02(latitude, longitude)
        limit = max(1, min(25, int(limit)))
        payload = await self._request(
            "/v3/place/around",
            {
                "location": f"{amap_lon},{amap_lat}",
                "keywords": keyword.strip(),
                "radius": max(0, min(50_000, int(radius_meters))),
                "offset": limit,
                "page": 1,
                "extensions": "base",
                "sortrule": "distance",
            },
        )
        raw_places = payload.get("pois")
        raw_places = raw_places if isinstance(raw_places, list) else []
        places: list[dict[str, Any]] = []
        for raw in raw_places[:limit]:
            if not isinstance(raw, dict):
                continue
            places.append(
                {
                    "id": _text(raw.get("id")),
                    "name": _text(raw.get("name")) or "未命名地点",
                    "type": _text(raw.get("type")),
                    "address": _text(raw.get("address")),
                    "distance_meters": _number(raw.get("distance")),
                    "amap_coordinate": _coordinate(raw.get("location")),
                }
            )
        warning = self.warning(latitude, longitude)
        if not places and warning:
            raise AmapError("amap_no_result", f"附近没有高德结果。{warning}")
        return {
            "places": places,
            "amap_center": {"latitude": amap_lat, "longitude": amap_lon},
            "coverage_warning": warning,
        }

    async def geocode(self, address: str, city: str = "") -> dict[str, float]:
        if not address.strip():
            raise AmapError("amap_no_result", "目的地不能为空")
        payload = await self._request(
            "/v3/geocode/geo", {"address": address.strip(), "city": city.strip()}
        )
        geocodes = payload.get("geocodes")
        first = geocodes[0] if isinstance(geocodes, list) and geocodes else None
        coordinate = _coordinate(first.get("location")) if isinstance(first, dict) else None
        if coordinate is None:
            raise AmapError("amap_no_result", "高德没有找到该目的地；济州岛地名建议改用经纬度。")
        return coordinate

    async def route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
    ) -> dict[str, Any]:
        origin_amap = wgs84_to_gcj02(*origin)
        destination_amap = wgs84_to_gcj02(*destination)
        warning = self.warning(*origin) or self.warning(*destination)
        return await self._route_amap(origin_amap, destination_amap, mode, warning)

    async def route_to_address(
        self,
        origin: tuple[float, float],
        destination_address: str,
        mode: str,
        city: str = "",
    ) -> dict[str, Any]:
        destination = await self.geocode(destination_address, city)
        origin_amap = wgs84_to_gcj02(*origin)
        # AMap geocoding already returns GCJ-02; never transform it twice.
        destination_amap = (destination["latitude"], destination["longitude"])
        return await self._route_amap(origin_amap, destination_amap, mode, self.warning(*origin))

    async def _route_amap(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        mode: str,
        warning: str | None,
    ) -> dict[str, Any]:
        if mode not in {"walking", "driving"}:
            raise AmapError("invalid_input", "mode 只能是 walking 或 driving")
        payload = await self._request(
            "/v3/direction/walking" if mode == "walking" else "/v3/direction/driving",
            {
                "origin": f"{origin[1]},{origin[0]}",
                "destination": f"{destination[1]},{destination[0]}",
                "extensions": "base",
            },
        )
        route = payload.get("route")
        paths = route.get("paths") if isinstance(route, dict) else None
        first = paths[0] if isinstance(paths, list) and paths else None
        distance = _number(first.get("distance")) if isinstance(first, dict) else None
        duration = _number(first.get("duration")) if isinstance(first, dict) else None
        if distance is None or duration is None:
            raise AmapError("amap_no_result", f"高德没有返回可用路线。{warning or ''}".strip())
        raw_steps = first.get("steps") if isinstance(first, dict) else []
        raw_steps = raw_steps if isinstance(raw_steps, list) else []
        steps = []
        for raw in raw_steps:
            if not isinstance(raw, dict):
                continue
            steps.append(
                {
                    "instruction": _text(raw.get("instruction")) or "继续前行",
                    "road": _text(raw.get("road")),
                    "distance_meters": _number(raw.get("distance")),
                    "duration_seconds": _number(raw.get("duration")),
                }
            )
        origin_dict = {"latitude": origin[0], "longitude": origin[1]}
        destination_dict = {"latitude": destination[0], "longitude": destination[1]}
        navigation_url = "https://uri.amap.com/navigation?" + urlencode(
            {
                "from": f"{origin[1]},{origin[0]},当前位置",
                "to": f"{destination[1]},{destination[0]},目的地",
                "mode": "walk" if mode == "walking" else "car",
                "coordinate": "gaode",
                "callnative": "1",
            }
        )
        return {
            "mode": mode,
            "distance_meters": distance,
            "duration_seconds": duration,
            "steps": steps,
            "amap_origin": origin_dict,
            "amap_destination": destination_dict,
            "navigation_url": navigation_url,
            "coverage_warning": warning,
        }

