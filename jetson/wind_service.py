"""ANKEBUT Jetson - wind service.

Given a (lat, lon), fetches current wind from a weather API and returns a
dict shaped for the GCS fire-event message:

    {"direction": "NE", "direction_deg": 45, "speed_ms": 4.2}

Uses only the standard library (urllib) so there is no `requests` dependency.
Every call is bounded by a timeout so it can never stall the mission loop
(the mission controller still calls this on its own thread, just in case).

Providers:
  * "open-meteo"      - no API key required (good for testing)
  * "openweathermap"  - needs WEATHER_API_KEY
"""

import json
import urllib.parse
import urllib.request

_COMPASS_16 = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def deg_to_compass(deg):
    """Meteorological degrees (direction the wind comes FROM) -> 16-pt label."""
    idx = int((float(deg) % 360) / 22.5 + 0.5) % 16
    return _COMPASS_16[idx]


def _parse_open_meteo(data):
    cw = data.get("current_weather", {}) or {}
    speed = cw.get("windspeed")            # m/s (we request wind_speed_unit=ms)
    deg = cw.get("winddirection")
    if speed is None or deg is None:
        return None
    return {
        "direction": deg_to_compass(deg),
        "direction_deg": int(round(deg)),
        "speed_ms": round(float(speed), 1),
    }


def _parse_owm(data):
    wind = data.get("wind", {}) or {}
    speed = wind.get("speed")              # m/s when units=metric
    deg = wind.get("deg")
    if speed is None or deg is None:
        return None
    return {
        "direction": deg_to_compass(deg),
        "direction_deg": int(round(deg)),
        "speed_ms": round(float(speed), 1),
    }


class WindService:
    def __init__(self, provider="open-meteo", api_key="", timeout=4):
        self.provider = provider
        self.api_key = api_key
        self.timeout = timeout

    def _build_url(self, lat, lon):
        if self.provider == "openweathermap":
            q = urllib.parse.urlencode({
                "lat": lat, "lon": lon, "appid": self.api_key, "units": "metric",
            })
            return f"https://api.openweathermap.org/data/2.5/weather?{q}"
        # default: open-meteo
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "current_weather": "true", "wind_speed_unit": "ms",
        })
        return f"https://api.open-meteo.com/v1/forecast?{q}"

    def get_wind(self, lat, lon):
        """Return the wind dict, or None on any failure (never raises)."""
        url = self._build_url(lat, lon)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print("[WIND] fetch failed:", e)
            return None

        if self.provider == "openweathermap":
            return _parse_owm(data)
        return _parse_open_meteo(data)


if __name__ == "__main__":
    # Note: needs internet. Istanbul-ish coordinates.
    svc = WindService(provider="open-meteo")
    print(svc.get_wind(41.0082, 28.9784))
