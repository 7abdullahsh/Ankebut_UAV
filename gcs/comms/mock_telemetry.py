"""ANKEBUT GCS - Mock telemetri saglayici (Faz 1).

Faz 4'te gercek MAVLink dinleyicisi ile degistirilecek.
Ayni sinyal yapisini koruyor, boylece arayuz kodu degismeyecek.
"""
import math
import random
from PySide6.QtCore import QObject, QTimer, Signal


class TelemetryProvider(QObject):
    """Sahte ama gercekci telemetri verisi uretir."""

    telemetry_updated = Signal(dict)

    def __init__(self, update_interval_ms=250):
        super().__init__()
        self._t = 0.0
        self._dt = update_interval_ms / 1000.0

        # Baslangic durumu
        self._battery = 95.0
        # Istanbul merkez koordinatlari (yarisma icin varsayilan)
        self._lat0 = 41.0370
        self._lon0 = 28.9857

        # Timer
        self._timer = QTimer(self)
        self._timer.setInterval(update_interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _tick(self):
        self._t += self._dt

        # Batarya yavasca dusuyor
        battery = max(15.0, 95.0 - self._t * 0.15)
        battery_voltage = 11.1 + (battery / 100.0) * 1.5

        # Irtifa: 5sn'de 30m'ye cikiyor, sonra salinma
        if self._t < 5:
            altitude = self._t * 6
        else:
            altitude = 30.0 + 5.0 * math.sin(self._t * 0.3) + random.uniform(-0.5, 0.5)

        # Hiz: kalkis sonrasi 10 m/s civarinda
        if self._t < 5:
            speed = self._t * 2
        else:
            speed = 10.0 + 4.0 * math.sin(self._t * 0.2)

        # Yon: yavasca donuyor
        heading = (self._t * 8) % 360

        # Konum: ~100m yaricapli daire
        radius_deg = 0.0009
        lat = self._lat0 + radius_deg * math.cos(self._t * 0.1)
        lon = self._lon0 + radius_deg * math.sin(self._t * 0.1)

        # Ucus modu: zamana gore
        if self._t < 5:
            mode, armed = "STABILIZE", True
        elif self._t < 90:
            mode, armed = "AUTO", True
        else:
            mode, armed = "RTL", True

        data = {
            "battery_percent": round(battery, 1),
            "battery_voltage": round(battery_voltage, 2),
            "altitude_m": round(altitude, 1),
            "ground_speed_ms": round(speed, 1),
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "satellites": 12,
            "gps_fix": "3D",
            "flight_mode": mode,
            "armed": armed,
            "heading_deg": round(heading, 1),
        }
        self.telemetry_updated.emit(data)