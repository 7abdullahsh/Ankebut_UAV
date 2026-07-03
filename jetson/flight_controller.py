"""ANKEBUT Jetson - flight controller (MAVLink).

Thin wrapper around pymavlink for the things the mission needs:
  * connect to the autopilot (Pixhawk now, Cube Orange later - just a config
    string change)
  * read live position / altitude / heading / mode (cached by a background
    reader thread so reads are instant and never block)
  * switch flight mode (AUTO / GUIDED / RTL ...)
  * command a GUIDED position/altitude (used for the optional analysis climb)

This file cannot be exercised without a real autopilot or SITL, so the logic
here is written carefully but must be tested on the bench (SITL is ideal:
`sim_vehicle.py -v ArduCopter` then MAVLINK_CONNECTION = "udp:127.0.0.1:14550").
"""

import threading
import time

from pymavlink import mavutil


class FlightController:
    def __init__(self, connection_string, baud=921600):
        self.connection_string = connection_string
        self.baud = baud
        self.master = None

        self._lock = threading.Lock()
        self._reader = None
        self._running = False

        # cached telemetry
        self._lat = None
        self._lon = None
        self._alt_m = None          # relative altitude (AGL), metres
        self._heading_deg = None
        self._mode = None
        self._mission_seq = None
        self._mission_total = None

    # ------------------------------------------------------------------
    def connect(self, wait_heartbeat=True):
        """Open the link and start the background reader."""
        if self.connection_string.startswith(("udp:", "tcp:", "udpin:")):
            self.master = mavutil.mavlink_connection(self.connection_string)
        else:
            self.master = mavutil.mavlink_connection(
                self.connection_string, baud=self.baud)

        if wait_heartbeat:
            print("[FC] waiting for heartbeat...")
            self.master.wait_heartbeat()
            print(f"[FC] heartbeat from sys {self.master.target_system} "
                  f"comp {self.master.target_component}")

        self._running = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        while self._running:
            msg = self.master.recv_match(blocking=True, timeout=1.0)
            if msg is None:
                continue
            t = msg.get_type()
            with self._lock:
                if t == "GLOBAL_POSITION_INT":
                    self._lat = msg.lat / 1e7
                    self._lon = msg.lon / 1e7
                    self._alt_m = msg.relative_alt / 1000.0
                    self._heading_deg = msg.hdg / 100.0
                elif t == "HEARTBEAT":
                    try:
                        self._mode = mavutil.mode_string_v10(msg)
                    except Exception:
                        self._mode = None
                elif t == "MISSION_CURRENT":
                    self._mission_seq = msg.seq

    # ------------------------------------------------------------------
    def get_position(self):
        """Latest fix as {'lat','lon','alt_m','heading_deg'} or None."""
        with self._lock:
            if self._lat is None or self._lon is None:
                return None
            return {
                "lat": self._lat,
                "lon": self._lon,
                "alt_m": self._alt_m if self._alt_m is not None else 0.0,
                "heading_deg": self._heading_deg
                if self._heading_deg is not None else 0.0,
            }

    @property
    def mode(self):
        with self._lock:
            return self._mode

    def is_mode(self, name):
        m = self.mode
        return m is not None and m.upper() == name.upper()

    # ------------------------------------------------------------------
    def set_mode(self, mode_name):
        """Request a flight-mode change (AUTO / GUIDED / RTL ...)."""
        mapping = self.master.mode_mapping() or {}
        mode_id = mapping.get(mode_name.upper())
        if mode_id is None:
            print(f"[FC] unknown mode '{mode_name}'. Known: {list(mapping)}")
            return False
        self.master.mav.set_mode_send(
            self.master.target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_id,
        )
        print(f"[FC] mode -> {mode_name}")
        return True

    def goto(self, lat, lon, alt_m):
        """Command a GUIDED target position (relative/AGL altitude)."""
        self.master.mav.set_position_target_global_int_send(
            0,                                   # time_boot_ms
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            0b0000111111111000,                  # use position only
            int(lat * 1e7), int(lon * 1e7), float(alt_m),
            0, 0, 0,   # vx, vy, vz
            0, 0, 0,   # ax, ay, az
            0, 0,      # yaw, yaw_rate
        )

    def climb_to(self, target_alt_m):
        """Hold current lat/lon, change to target relative altitude."""
        pos = self.get_position()
        if pos is None:
            return False
        self.goto(pos["lat"], pos["lon"], target_alt_m)
        return True

    def at_altitude(self, target_alt_m, tol=0.6):
        pos = self.get_position()
        if pos is None:
            return False
        return abs(pos["alt_m"] - target_alt_m) <= tol

    def is_mission_complete(self):
        """Hook: wire this to your real mission end-condition.

        ArduPilot exposes mission progress via MISSION_CURRENT and the total
        item count. A common signal is the autopilot switching out of AUTO at
        the end, or MISSION_CURRENT.seq reaching the last item. Left
        conservative (False) so the mission never RTLs unexpectedly until you
        define the condition for your flight plan.
        """
        return False

    # ------------------------------------------------------------------
    def arm(self):
        self.master.mav.command_long_send(
            self.master.target_system, self.master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
            1, 0, 0, 0, 0, 0, 0)

    def close(self):
        self._running = False
        if self._reader is not None:
            self._reader.join(timeout=2.0)
        if self.master is not None:
            self.master.close()
