"""Dự báo thời tiết từ Open-Meteo (miễn phí, không cần API key, dùng phi thương mại).

Thời tiết là SỐ LIỆU chứ không phải tin tức: câu hỏi "hôm nay mưa không, mấy độ" được trả
lời bằng dự báo thật thay vì để LLM đoán.
"""

import logging
import re
import time
from datetime import date, datetime

from assistant.core.config import Settings
from assistant.ingestion.text import fold

log = logging.getLogger(__name__)

API = "https://api.open-meteo.com/v1/forecast"
WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

# Mã thời tiết WMO -> (mô tả, biểu tượng)
WMO = {
    0: ("trời quang", "☀️"), 1: ("ít mây", "🌤️"), 2: ("có mây", "⛅"), 3: ("nhiều mây", "☁️"),
    45: ("sương mù", "🌫️"), 48: ("sương mù", "🌫️"),
    51: ("mưa phùn nhẹ", "🌦️"), 53: ("mưa phùn", "🌦️"), 55: ("mưa phùn dày", "🌧️"),
    56: ("mưa phùn lạnh", "🌧️"), 57: ("mưa phùn lạnh", "🌧️"),
    61: ("mưa nhỏ", "🌦️"), 63: ("mưa vừa", "🌧️"), 65: ("mưa to", "🌧️"),
    66: ("mưa lạnh", "🌧️"), 67: ("mưa lạnh to", "🌧️"),
    71: ("tuyết nhẹ", "🌨️"), 73: ("tuyết", "🌨️"), 75: ("tuyết dày", "🌨️"), 77: ("tuyết hạt", "🌨️"),
    80: ("mưa rào nhẹ", "🌦️"), 81: ("mưa rào", "🌧️"), 82: ("mưa rào rất to", "⛈️"),
    85: ("mưa tuyết", "🌨️"), 86: ("mưa tuyết to", "🌨️"),
    95: ("dông", "⛈️"), 96: ("dông kèm mưa đá", "⛈️"), 99: ("dông kèm mưa đá lớn", "⛈️"),
}  # fmt: skip

_WEATHER_Q = re.compile(
    r"(thoi tiet|du bao|nhiet do|bao nhieu do|may do|troi (?:mua|nang|lanh|nong|ret|dep)"
    r"|co mua|mua khong|nang khong|lanh khong|nong khong|ret khong|mang o|ao mua|ao khoac"
    r"|do am)"
)


def is_weather_question(question: str) -> bool:
    return bool(_WEATHER_Q.search(fold(question)))


def wmo(code) -> tuple[str, str]:
    return WMO.get(int(code) if code is not None else -1, ("không rõ", "🌡️"))


def _r(x, nd=0):
    return None if x is None else round(float(x), nd) if nd else int(round(float(x)))


class WeatherService:
    def __init__(self, settings: Settings, http_get=None) -> None:
        self._s = settings
        self._get = http_get  # hàm (url, params) -> dict; mặc định httpx
        self._cache: tuple[float, dict] | None = None

    def _fetch(self) -> dict:
        params = {
            "latitude": self._s.weather_lat,
            "longitude": self._s.weather_lon,
            "timezone": self._s.timezone,
            "forecast_days": 7,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
            "weather_code,wind_speed_10m",
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
            "precipitation_probability_max,wind_speed_10m_max",
        }
        if self._get:
            return self._get(API, params)
        import httpx

        r = httpx.get(API, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def data(self) -> dict:
        """Dữ liệu dự báo, lưu tạm WEATHER_CACHE_MIN phút."""
        now = time.monotonic()
        if self._cache and now - self._cache[0] < self._s.weather_cache_min * 60:
            return self._cache[1]
        data = self._fetch()
        self._cache = (now, data)
        return data

    def summary(self) -> dict:
        """Gọn cho giao diện: hiện tại + 7 ngày."""
        d = self.data()
        cur = d.get("current", {})
        desc, icon = wmo(cur.get("weather_code"))
        daily = d.get("daily", {})
        days = []
        today = date.fromisoformat(daily["time"][0]) if daily.get("time") else None
        for i, t in enumerate(daily.get("time", [])):
            day = date.fromisoformat(t)
            ddesc, dicon = wmo(daily["weather_code"][i])
            days.append(
                {
                    "date": t,
                    "label": "Hôm nay" if day == today else WEEKDAYS[day.weekday()],
                    "tmin": _r(daily["temperature_2m_min"][i]),
                    "tmax": _r(daily["temperature_2m_max"][i]),
                    "rain_mm": _r(daily["precipitation_sum"][i], 1),
                    "rain_prob": _r(daily["precipitation_probability_max"][i]),
                    "desc": ddesc,
                    "icon": dicon,
                }
            )
        return {
            "location": self._s.weather_location,
            "updated": cur.get("time"),
            "current": {
                "temp": _r(cur.get("temperature_2m")),
                "feels": _r(cur.get("apparent_temperature")),
                "humidity": _r(cur.get("relative_humidity_2m")),
                "wind": _r(cur.get("wind_speed_10m")),
                "rain_mm": _r(cur.get("precipitation"), 1),
                "desc": desc,
                "icon": icon,
            },
            "days": days,
        }

    def describe(self) -> str:
        """Văn bản dự báo đưa cho LLM làm nguồn (số liệu thật, có ngày giờ)."""
        s = self.summary()
        d = self.data()
        c = s["current"]
        lines = [
            f"Dự báo thời tiết {s['location']} (nguồn Open-Meteo, số liệu lúc "
            f"{(s['updated'] or '').replace('T', ' ')}).",
            f"Hiện tại: {c['temp']}°C, cảm giác như {c['feels']}°C, độ ẩm {c['humidity']}%, "
            f"gió {c['wind']} km/h, {c['desc']}, lượng mưa {c['rain_mm']} mm.",
        ]
        hourly = d.get("hourly", {})
        times = hourly.get("time", [])
        if times and s["updated"]:
            now = datetime.fromisoformat(s["updated"])
            hours = [
                f"{t[11:13]}h {_r(hourly['temperature_2m'][i])}°C "
                f"{wmo(hourly['weather_code'][i])[0]}, khả năng mưa "
                f"{_r(hourly['precipitation_probability'][i])}%"
                for i, t in enumerate(times)
                if now < datetime.fromisoformat(t) and t[:10] == s["updated"][:10] and i % 3 == 0
            ]
            if hours:
                lines.append("Các giờ còn lại hôm nay: " + "; ".join(hours) + ".")
        lines.append("Dự báo các ngày tới:")
        for day in s["days"]:
            dt = date.fromisoformat(day["date"])
            lines.append(
                f"- {day['label']} {dt:%d/%m}: {day['tmin']}–{day['tmax']}°C, {day['desc']}, "
                f"lượng mưa {day['rain_mm']} mm, khả năng mưa {day['rain_prob']}%."
            )
        return "\n".join(lines)
