# -*- coding: utf-8 -*-
"""叙事事实校验测试 (R2, 济州岛验收项 4/5/6)。"""
from __future__ import annotations

from nowhere import facts
from nowhere.facts import (
    check_moment,
    check_temperature,
    parse_hour,
    validate,
)

JEJU_MID = (33.3803, 126.5483)


def _env(**over):
    base = {
        "weather": {"temp_c": 21.7, "source": "openmeteo", "precip": "none",
                    "wind_ms": 2.0, "humidity": 60, "feels_c": 21.0, "text": "晴",
                    "cloud": 10.0},
        "sky": {"phase": "day", "sun_alt": 30.0},
        "surface": "forest",
        "elevation": 300,
        "water_features": [],
        "lat": JEJU_MID[0], "lon": JEJU_MID[1],
        "local_hour": 14,
    }
    base.update(over)
    return base


class TestTemperature:
    def test_conflicting_six_degrees_replaced(self):
        """验收 4: 21-22°C 天气不能出现 6°C 冲突文本。"""
        text = "天色转阴。气温降到了 6 度。风也起来了。"
        cleaned, report = validate(text, _env())
        assert "6" not in cleaned
        assert any(r["rule"] == "temperature" for r in report)

    def test_matching_temp_kept(self):
        text = "空气 21 度,不冷不热。"
        cleaned, report = validate(text, _env())
        assert "21" in cleaned
        assert report == []

    def test_climate_source_no_numbers(self):
        """气候估算来源不得输出具体数字温度。"""
        env = _env(weather={"temp_c": 12.0, "source": "climate"})
        text = "空气 12 度。"
        cleaned, report = validate(text, env)
        assert "12" not in cleaned
        assert any(r["rule"] == "temperature" for r in report)

    def test_slope_degrees_not_temperature(self):
        """「坡 12 度」是坡度不是气温, 不能被误删。"""
        text = "坡 12 度,脚下碎石。"
        cleaned, report = validate(text, _env())
        assert "12" in cleaned
        assert report == []

    def test_sst_degrees_not_air_temperature(self):
        text = "海水 18 度,凉。"
        cleaned, report = validate(text, _env())
        assert "18" in cleaned
        assert report == []


class TestTime:
    def test_evening_no_dawn_six(self):
        """验收 5: 傍晚不能出现「清晨六点」。"""
        env = _env(local_hour=18, sky={"phase": "day", "sun_alt": 5.0})
        text = "你出门了。清晨六点,路上没有人。"
        cleaned, report = validate(text, env)
        assert "清晨六点" not in cleaned
        assert any(r["rule"] == "time" for r in report)

    def test_morning_six_ok_when_it_is_morning(self):
        env = _env(local_hour=6, sky={"phase": "day", "sun_alt": 1.0})
        text = "清晨六点,天刚亮。"
        cleaned, report = validate(text, env)
        assert "清晨六点" in cleaned
        assert report == []

    def test_moment_word_vs_hour(self):
        assert check_moment("清晨六点出门。", None, "day") is None
        assert check_moment("傍晚六点出门。", None, "day") is None
        assert check_moment("清晨晚上八点。", None, "day") is not None  # 时刻词与钟点矛盾

    def test_parse_hour(self):
        assert parse_hour("六点") == 6
        assert parse_hour("16点") == 16
        assert parse_hour("二十三点了") == 23
        assert parse_hour("没有钟点") is None


class TestPlaceDistance:
    def test_distant_landmark_not_closeup(self):
        """验收 6: 距离较远的具体地标不能写成眼前景物。"""
        env = _env(lat=39.9042, lon=116.4074)  # Beijing
        text = "埃菲尔铁塔就在你眼前。你抬头看。"
        cleaned, report = validate(text, env)
        assert "埃菲尔铁塔" not in cleaned
        assert any(r["rule"] == "place_distance" for r in report)

    def test_far_view_mention_allowed(self):
        """远景提及地标属于文学自由, 不删除。"""
        env = _env(lat=39.9042, lon=116.4074)
        text = "你想起埃菲尔铁塔在很远的地方。"
        cleaned, report = validate(text, env)
        assert "埃菲尔铁塔" in cleaned


class TestTerrainCompat:
    def test_coast_text_in_deep_inland(self):
        env = _env(lat=43.82, lon=87.61, surface="grass")  # Urumqi
        text = "海浪就拍在你脚边。"
        cleaned, report = validate(text, env)
        assert "海浪" not in cleaned
        assert any(r["rule"] == "terrain_compat" for r in report)

    def test_city_text_in_forest(self):
        env = _env(surface="forest")  # Jeju mid, no city nearby
        text = "霓虹灯就在你眼前闪。"
        cleaned, report = validate(text, env)
        assert "霓虹" not in cleaned
        assert any(r["rule"] == "terrain_compat" for r in report)

    def test_coast_text_near_jeju_coast_allowed(self):
        env = _env(lat=33.24, lon=126.56, surface="forest")
        text = "海浪就在脚边。"
        cleaned, report = validate(text, env)
        assert "海浪" in cleaned  # 2km 内就是海, 合法
        assert report == []


class TestObservedEnvironment:
    def test_structure(self):
        obs = facts.observed_environment(_env(), *JEJU_MID)
        assert obs["position"] == {"lat": JEJU_MID[0], "lon": JEJU_MID[1]}
        assert obs["weather"]["source"] == "openmeteo"
        assert "coast" in obs
        assert obs["coast"]["nearest_ocean"]["source"] == "landmask"
