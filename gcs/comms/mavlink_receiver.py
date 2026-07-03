"""ANKEBUT GCS - MAVLink telemetri alicisi (Faz 2 Gun 3).

Mock provider ile birebir ayni arayuze sahip:
  - TelemetryProvider sinifi
  - telemetry_updated(dict) sinyali
  - start() / stop() metodlari
  - dict anahtar isimleri (battery_percent, lat, lon, flight_mode, ...)

Faz 4'te transport (UDP -> Serial) degisecek, arayuz ayni kalacak.
"""
import time

from PySide6.QtCore import QObject, QThread, Signal
from pymavlink import mavutil


# ArduCopter custom_mode -> string (mock ile uyumlu)
ARDUCOPTER_MODES = {
    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO",
    4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE",
    9: "LAND", 13: "TAKEOFF",
}

# MAVLink GPS fix tipleri -> mock'taki gibi string
GPS_FIX_MAP = {
    0: "NO_GPS", 1: "NO_FIX", 2: "2D", 3: "3D",
    4: "DGPS", 5: "RTK_FLOAT", 6: "RTK_FIXED", 7: "STATIC",
}


class _ReceiverThread(QThread):
    """UDP'den MAVLink mesajlari dinler, durumu biriktirir, periyodik emit eder."""
    
    telemetry_updated = Signal(dict)
    
    def __init__(self, port: int, update_interval_ms: int = 250, parent=None):
        super().__init__(parent)
        self._port = port
        self._emit_interval = update_interval_ms / 1000.0
        self._running = False
        self._has_data = False  # ilk HEARTBEAT gelmeden emit etmeyiz
        
        # Birikmis durum - anahtar isimleri mock_telemetry ile birebir ayni
        self._state = {
            "battery_percent": 0.0,
            "battery_voltage": 0.0,
            "altitude_m": 0.0,
            "ground_speed_ms": 0.0,
            "lat": 0.0,
            "lon": 0.0,
            "satellites": 12,           # GPS_RAW_INT henuz gelmiyor, default
            "gps_fix": "3D",            # default
            "flight_mode": "UNKNOWN",
            "armed": False,
            "heading_deg": 0.0,
        }
    
    def run(self):
        # 0.0.0.0 = tum interface'lerden dinle (iki laptop testinde gerekli)
        try:
            conn = mavutil.mavlink_connection(
                f'udpin:0.0.0.0:{self._port}',
                source_system=255,      # yer istasyonu standardi
                source_component=190,   # GCS komponenti
            )
        except Exception as e:
            print(f"[MavlinkReceiver] Bind hatasi: {e}")
            return
        
        print(f"[MavlinkReceiver] udp://0.0.0.0:{self._port} dinleniyor...")
        self._running = True
        last_emit = time.monotonic()
        
        while self._running and not self.isInterruptionRequested():
            # 100ms timeout - cok beklemeden dongu donsun ki emit ve interrupt'i kontrol edebilelim
            msg = conn.recv_match(blocking=True, timeout=0.1)
            
            if msg is not None:
                self._handle_message(msg)
            
            # Periyodik emit (mock_telemetry ile ayni 250ms kadansi)
            now = time.monotonic()
            if self._has_data and (now - last_emit) >= self._emit_interval:
                self.telemetry_updated.emit(dict(self._state))   # kopya yolla
                last_emit = now
        
        try:
            conn.close()
        except Exception:
            pass
        print("[MavlinkReceiver] Durduruldu.")
    
    def stop(self):
        self._running = False
        self.requestInterruption()
    
    def _handle_message(self, msg):
        msg_type = msg.get_type()
        
        if msg_type == "BAD_DATA":
            return
        
        if msg_type == "HEARTBEAT":
            self._has_data = True
            self._state["flight_mode"] = ARDUCOPTER_MODES.get(msg.custom_mode, "UNKNOWN")
            armed_flag = mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
            self._state["armed"] = bool(msg.base_mode & armed_flag)
        
        elif msg_type == "SYS_STATUS":
            self._state["battery_voltage"] = round(msg.voltage_battery / 1000.0, 2)  # mV -> V
            self._state["battery_percent"] = round(float(msg.battery_remaining), 1)
        
        elif msg_type == "GLOBAL_POSITION_INT":
            self._state["lat"] = round(msg.lat / 1e7, 7)
            self._state["lon"] = round(msg.lon / 1e7, 7)
            self._state["altitude_m"] = round(msg.alt / 1000.0, 1)         # mm -> m
            self._state["heading_deg"] = round(msg.hdg / 100.0, 1)         # cdeg -> deg
        
        elif msg_type == "VFR_HUD":
            self._state["ground_speed_ms"] = round(msg.groundspeed, 1)
        
        elif msg_type == "GPS_RAW_INT":
            self._state["satellites"] = int(msg.satellites_visible)
            self._state["gps_fix"] = GPS_FIX_MAP.get(msg.fix_type, "UNKNOWN")


class TelemetryProvider(QObject):
    """Mock TelemetryProvider ile ayni arayuz: telemetry_updated, start, stop."""
    
    telemetry_updated = Signal(dict)
    
    def __init__(self, update_interval_ms: int = 250, udp_port: int = 14550, parent=None):
        super().__init__(parent)
        self._thread = _ReceiverThread(port=udp_port, update_interval_ms=update_interval_ms)
        # Thread sinyalini disariya forward et
        self._thread.telemetry_updated.connect(self.telemetry_updated)
    
    def start(self):
        self._thread.start()
    
    def stop(self):
        self._thread.stop()
        self._thread.wait(2000)