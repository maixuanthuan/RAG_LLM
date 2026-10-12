import hashlib
import math
import re

from assistant.ingestion.text import fold


class FakeEmbedder:
    """Embedding giả (túi từ băm) để test không cần model thật. Xác định, không dùng mạng."""

    model = "fake-embed"
    dim = 512

    def _vec(self, text: str) -> list[float]:
        tokens = re.findall(r"\w+", fold(text))
        grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        v = [0.0] * self.dim
        for g in grams:
            v[int(hashlib.md5(g.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


# Mẫu phản hồi Open-Meteo (rút gọn) để test thời tiết không cần mạng
WEATHER_SAMPLE = {
    "current": {
        "time": "2026-10-12T14:45",
        "temperature_2m": 29.4,
        "relative_humidity_2m": 74,
        "apparent_temperature": 33.1,
        "precipitation": 0.0,
        "weather_code": 3,
        "wind_speed_10m": 11.2,
    },
    "hourly": {
        "time": [f"2026-10-12T{h:02d}:00" for h in range(24)],
        "temperature_2m": [24 + (h % 12) * 0.5 for h in range(24)],
        "precipitation_probability": [10 * (h % 8) for h in range(24)],
        "weather_code": [80 if h >= 18 else 3 for h in range(24)],
    },
    "daily": {
        "time": [f"2026-10-{d}" for d in range(12, 19)],
        "weather_code": [80, 61, 3, 1, 0, 95, 2],
        "temperature_2m_max": [31.2, 29.0, 30.5, 31.0, 32.4, 28.1, 30.0],
        "temperature_2m_min": [24.1, 23.5, 24.0, 24.2, 25.0, 23.0, 24.0],
        "precipitation_sum": [3.2, 12.5, 0.0, 0.0, 0.0, 25.4, 0.4],
        "precipitation_probability_max": [60, 85, 10, 5, 0, 90, 20],
        "wind_speed_10m_max": [15.0, 20.1, 12.0, 10.0, 9.5, 30.2, 11.0],
    },
}
