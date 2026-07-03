"""ANKEBUT GCS - Fire/smoke detection & event panel.

Display-only panel. It consumes two kinds of messages from the Orin Nano
(delivered however you like - UDP/JSON, signal, queue) and turns them into
operator-facing information. It opens no sockets itself, so the same panel
works against the real Orin Nano feed or against the simulator at the bottom
of this file.

Message 1 - "detection"  (high frequency, ~10 Hz, one per processed frame):
    {
        "type": "detection",
        "ts": 1718900000.12,
        "state": "ANALYZING",          # PATROL|LOCKED|CLIMBING|ANALYZING|DESCENDING|RESUMING
        "analyze_remaining_s": 7,      # optional, only meaningful during ANALYZING
        "detections": [
            {"label": "fire",  "confidence": 0.92},
            {"label": "smoke", "confidence": 0.74}
        ],
        "drone": {"lat": 41.0200, "lon": 28.9800, "alt_m": 45.0, "heading_deg": 270}
    }
    -> feed to update_detection(); drives the banner, live chips and state badge.

Message 2 - "fire_event"  (low frequency, once per fire after the analysis):
    {
        "type": "fire_event",
        "ts": 1718900012.45,
        "event_id": "evt_0001",
        "fire_location": {"lat": 41.0213, "lon": 28.9841, "alt_m": 50.0},
        "terrain":  {"tree": 0.10, "green_grass": 0.10, "dry_grass": 0.60,
                     "water": 0.05, "soil": 0.15},
        "wind":     {"direction": "N", "direction_deg": 0, "speed_ms": 4.2},
        "risk":     {"level": "HIGH", "primary_fuel": "dry_grass", "note": "..."}
    }
    -> feed to register_event(); drives the "Last fire event" block and the log.
"""

import sys
import time
import json
import threading
import urllib.parse
import urllib.request

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QProgressBar,
    QListWidget, QListWidgetItem, QApplication, QSizePolicy
)


_WIND_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _fetch_wind_online(lat, lon, timeout=4):
    """GCS-side wind lookup (open-meteo, no API key). Returns a dict shaped
    like the Jetson's wind, or None on any failure. Runs on the GCS (laptop +
    phone hotspot) so the drone never needs internet - only the fire's GPS
    coordinate is sent over the radio link, and the wind is looked up here."""
    try:
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "current_weather": "true", "wind_speed_unit": "ms",
        })
        url = f"https://api.open-meteo.com/v1/forecast?{q}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        cw = data.get("current_weather", {}) or {}
        speed, deg = cw.get("windspeed"), cw.get("winddirection")
        if speed is None or deg is None:
            return None
        idx = int((float(deg) % 360) / 22.5 + 0.5) % 16
        return {"direction": _WIND_COMPASS[idx],
                "direction_deg": int(round(deg)),
                "speed_ms": round(float(speed), 1)}
    except Exception as e:
        print("[GCS-WIND] fetch failed:", e)
        return None

# ---- Palette (matches the rest of the GCS) ----
BG = "#1F3346"
BG_DARK = "#18293A"
FG = "#FFFFFF"
FG_MUTED = "#8FA8C2"
BORDER = "#3A5066"

COL_OK = "#27AE60"
COL_WARN = "#F39C12"
COL_FIRE = "#E74C3C"
COL_FIRE_2 = "#C0392B"
COL_SMOKE = "#7F8C8D"

# Segmentation class -> colour / label
TERRAIN_COLORS = {
    "tree": "#2E7D32",
    "green_grass": "#7CB342",
    "dry_grass": "#C9A227",
    "water": "#2980B9",
    "soil": "#8D6E63",
}
TERRAIN_LABELS = {
    "tree": "Tree",
    "green_grass": "Green grass",
    "dry_grass": "Dry grass",
    "water": "Water",
    "soil": "Soil",
}
TERRAIN_ORDER = ("tree", "green_grass", "dry_grass", "water", "soil")

RISK_COLORS = {"LOW": "#27AE60", "MEDIUM": "#F39C12", "HIGH": "#E74C3C"}

# Flight-sequence state -> (badge text, colour)
STATE_STYLES = {
    "PATROL": ("PATROL", "#34495E"),
    "LOCKED": ("FIRE LOCKED", "#C0392B"),
    "CLIMBING": ("CLIMBING", "#D68910"),
    "ANALYZING": ("ANALYZING", "#D68910"),
    "DESCENDING": ("DESCENDING", "#D68910"),
    "RESUMING": ("RESUMING", "#229954"),
}


class DetectionPanel(QWidget):
    # Emitted by the background wind-fetch thread; carries the wind dict back
    # to the GUI thread so the label can be updated safely.
    wind_ready = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"background-color: {BG};")
        self._fire_active = False
        self._pulse_on = False
        self._wind_seq = 0          # ignore stale fetches if a newer fire lands
        self._build_ui()
        self.wind_ready.connect(self._apply_wind)

        # Banner pulse - only animates while a fire is on screen.
        self._pulse = QTimer(self)
        self._pulse.setInterval(600)
        self._pulse.timeout.connect(self._do_pulse)

    # ---------- small builders ----------
    def _sep(self):
        s = QFrame()
        s.setFrameShape(QFrame.HLine)
        s.setStyleSheet(f"color: {BORDER}; background-color: {BORDER};")
        s.setFixedHeight(1)
        return s

    def _section(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {FG}; font-size: 11px; font-weight: bold; "
            f"padding-top: 6px;"
        )
        return lbl

    def _kv_row(self, key):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 1, 0, 1)
        k = QLabel(key)
        k.setStyleSheet(f"color: {FG}; font-size: 12px; font-weight: bold;")
        k.setFixedWidth(90)
        v = QLabel("--")
        v.setStyleSheet(f"color: {FG}; font-size: 13px; font-weight: bold;")
        h.addWidget(k)
        h.addWidget(v)
        h.addStretch()
        return row, v

    def _terrain_row(self, key):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 1, 0, 1)
        k = QLabel(TERRAIN_LABELS[key])
        k.setStyleSheet(f"color: {FG}; font-size: 11px; font-weight: bold;")
        k.setFixedWidth(90)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(True)
        bar.setFormat("%p%")
        bar.setFixedHeight(16)
        bar.setStyleSheet(
            f"QProgressBar {{ background-color: {BG_DARK}; "
            f"border: 1px solid {BORDER}; border-radius: 2px; "
            f"color: white; font-size: 10px; }} "
            f"QProgressBar::chunk {{ background-color: {TERRAIN_COLORS[key]}; "
            f"border-radius: 2px; }}"
        )
        h.addWidget(k)
        h.addWidget(bar)
        return row, bar

    # ---------- layout ----------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Top status bar: the flight-state badge and the fire-alert banner sit
        # SIDE BY SIDE as two boxes (badge on the left, banner fills the rest).
        # Both fixed height, so neither can ever overlap the other.
        top_row = QWidget()
        top_row.setFixedHeight(50)
        top_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tr = QHBoxLayout(top_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(8)

        self.state_badge = QLabel()
        self.state_badge.setAlignment(Qt.AlignCenter)
        self.state_badge.setFixedSize(145, 50)
        self._style_state("PATROL")
        tr.addWidget(self.state_badge)

        self.banner = QLabel("NO FIRE DETECTED")
        self.banner.setAlignment(Qt.AlignCenter)
        self.banner.setFixedHeight(50)
        self.banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._style_banner(COL_OK, "NO FIRE DETECTED")
        tr.addWidget(self.banner, 1)

        root.addWidget(top_row)

        # Drone position (left) + live detection chips (right) on ONE row, so
        # there is no wasted gap under the boxes.
        pos_row = QWidget()
        pr = QHBoxLayout(pos_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(6)
        self.pos_label = QLabel("Drone: --")
        self.pos_label.setStyleSheet(f"color: {FG}; font-size: 11px; font-weight: bold;")
        pr.addWidget(self.pos_label)
        pr.addStretch()
        self.chips_holder = QHBoxLayout()
        self.chips_holder.setSpacing(6)
        pr.addLayout(self.chips_holder)
        root.addWidget(pos_row)

        root.addWidget(self._sep())

        # ---- Last fire event ----
        root.addWidget(self._section("LAST FIRE EVENT"))
        loc_row, self.evt_loc_val = self._kv_row("Location")
        alt_row, self.evt_alt_val = self._kv_row("Altitude")
        wind_row, self.evt_wind_val = self._kv_row("Wind")
        root.addWidget(loc_row)
        root.addWidget(alt_row)
        root.addWidget(wind_row)

        # Segmentation breakdown
        root.addWidget(self._section("Surrounding area"))
        self.terrain_bars = {}
        for key in TERRAIN_ORDER:
            trow, bar = self._terrain_row(key)
            self.terrain_bars[key] = bar
            root.addWidget(trow)

        # Risk
        risk_wrap = QWidget()
        rh = QHBoxLayout(risk_wrap)
        rh.setContentsMargins(0, 4, 0, 0)
        rlbl = QLabel("Risk")
        rlbl.setStyleSheet(f"color: {FG}; font-size: 12px; font-weight: bold;")
        rlbl.setFixedWidth(90)
        self.risk_badge = QLabel()
        self.risk_badge.setAlignment(Qt.AlignCenter)
        self.risk_badge.setFixedHeight(22)
        self.risk_badge.setMinimumWidth(74)
        self._set_risk("--", "#34495E")
        rh.addWidget(rlbl)
        rh.addWidget(self.risk_badge)
        rh.addStretch()
        root.addWidget(risk_wrap)

        self.risk_note = QLabel("")
        self.risk_note.setWordWrap(True)
        self.risk_note.setStyleSheet(f"color: {FG_MUTED}; font-size: 11px;")
        root.addWidget(self.risk_note)

        root.addWidget(self._sep())

        # ---- Event log ----
        root.addWidget(self._section("EVENT LOG"))
        self.log = QListWidget()
        self.log.setStyleSheet(
            f"QListWidget {{ background-color: {BG_DARK}; "
            f"border: 1px solid {BORDER}; color: {FG}; font-size: 11px; }} "
            f"QListWidget::item {{ padding: 3px; border-bottom: 1px solid #294056; }}"
        )
        root.addWidget(self.log, stretch=1)

    # ---------- styling helpers ----------
    def _style_banner(self, color, text):
        self.banner.setText(text)
        self.banner.setStyleSheet(
            f"background-color: {color}; color: white; font-size: 20px; "
            f"font-weight: bold; border-radius: 4px;"
        )

    def _set_state_badge(self, text, color):
        self.state_badge.setText(text)
        self.state_badge.setStyleSheet(
            f"background-color: {color}; color: white; font-size: 13px; "
            f"font-weight: bold; border-radius: 4px; padding: 2px 6px;"
        )

    def _style_state(self, state):
        text, color = STATE_STYLES.get(state, (state, "#34495E"))
        self._set_state_badge(text, color)

    def _set_risk(self, text, color):
        self.risk_badge.setText(text)
        self.risk_badge.setStyleSheet(
            f"background-color: {color}; color: white; font-size: 11px; "
            f"font-weight: bold; border-radius: 3px; padding: 2px 10px;"
        )

    def _add_chip(self, text, color):
        chip = QLabel(text)
        chip.setStyleSheet(
            f"background-color: {color}; color: white; font-size: 11px; "
            f"font-weight: bold; border-radius: 10px; padding: 2px 10px;"
        )
        self.chips_holder.addWidget(chip)

    def _clear_chips(self):
        while self.chips_holder.count():
            item = self.chips_holder.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _set_fire_active(self, active):
        if active == self._fire_active:
            return
        self._fire_active = active
        if active:
            self._pulse.start()
        else:
            self._pulse.stop()

    def _do_pulse(self):
        self._pulse_on = not self._pulse_on
        self._style_banner(COL_FIRE if self._pulse_on else COL_FIRE_2,
                            "FIRE DETECTED")

    # ---------- public API ----------
    def update_detection(self, msg):
        """Handle a high-frequency 'detection' message."""
        dets = msg.get("detections", []) or []
        labels = [str(d.get("label", "")).lower() for d in dets]
        has_fire = "fire" in labels
        has_smoke = "smoke" in labels

        if has_fire:
            self._set_fire_active(True)
            self._style_banner(COL_FIRE, "FIRE DETECTED")
        elif has_smoke:
            self._set_fire_active(False)
            self._style_banner(COL_WARN, "SMOKE DETECTED")
        else:
            self._set_fire_active(False)
            self._style_banner(COL_OK, "NO FIRE DETECTED")

        # Live chips
        self._clear_chips()
        for d in dets:
            lbl = str(d.get("label", "?")).lower()
            conf = float(d.get("confidence", 0.0) or 0.0)
            color = COL_FIRE if lbl == "fire" else (
                COL_SMOKE if lbl == "smoke" else "#34495E")
            self._add_chip(f"{lbl.upper()} {conf * 100:.0f}%", color)

        # Flight state (+ analyze countdown)
        state = msg.get("state", "PATROL")
        remaining = msg.get("analyze_remaining_s")
        if state == "ANALYZING" and remaining is not None:
            _, color = STATE_STYLES["ANALYZING"]
            self._set_state_badge(f"ANALYZING {int(remaining)}s", color)
        else:
            self._style_state(state)

        # Drone position
        drone = msg.get("drone", {}) or {}
        lat, lon, alt = drone.get("lat"), drone.get("lon"), drone.get("alt_m")
        if lat is not None and lon is not None:
            txt = f"Drone: {lat:.5f}, {lon:.5f}"
            if alt is not None:
                txt += f"   alt {alt:.0f} m"
            self.pos_label.setText(txt)

    def register_event(self, msg):
        """Handle a 'fire_event' packet: fill details + append to the log."""
        loc = msg.get("fire_location", {}) or {}
        lat, lon, alt = loc.get("lat"), loc.get("lon"), loc.get("alt_m")
        if lat is not None and lon is not None:
            self.evt_loc_val.setText(f"{lat:.5f}, {lon:.5f}")
        if alt is not None:
            self.evt_alt_val.setText(f"{alt:.0f} m")

        # Wind: prefer what the Jetson sent. If it's empty (the drone had no
        # internet, which is normal in the field), fetch it here on the GCS
        # using the fire's GPS coordinate.
        wind = msg.get("wind", {}) or {}
        self._wind_seq += 1
        if wind.get("speed_ms") is not None:
            self._apply_wind(wind)
        elif lat is not None and lon is not None:
            self.evt_wind_val.setText("checking…")
            self._fetch_wind_gcs(lat, lon, self._wind_seq)
        else:
            self.evt_wind_val.setText("--")

        terrain = msg.get("terrain", {}) or {}
        for key, bar in self.terrain_bars.items():
            frac = float(terrain.get(key, 0.0) or 0.0)
            bar.setValue(int(round(frac * 100)))

        risk = msg.get("risk", {}) or {}
        level = str(risk.get("level", "--")).upper()
        self._set_risk(level, RISK_COLORS.get(level, "#34495E"))
        self.risk_note.setText(risk.get("note", ""))

        self._append_log(msg)

    def _apply_wind(self, wind):
        """Update the Wind label (runs on the GUI thread)."""
        ws = wind.get("speed_ms")
        if ws is not None:
            wd = wind.get("direction", "--")
            self.evt_wind_val.setText(f"{wd}  {ws:.1f} m/s")
        else:
            self.evt_wind_val.setText("no internet?")

    def _fetch_wind_gcs(self, lat, lon, seq):
        """Fetch wind for the fire's GPS location in the background, then update
        the label via wind_ready. Ignores a result if a newer fire arrived."""
        def _w():
            w = _fetch_wind_online(lat, lon) or {}
            if seq == self._wind_seq:
                self.wind_ready.emit(w)
        threading.Thread(target=_w, daemon=True).start()

    def _append_log(self, msg):
        loc = msg.get("fire_location", {}) or {}
        lat, lon = loc.get("lat"), loc.get("lon")
        risk = msg.get("risk", {}) or {}
        level = str(risk.get("level", "?")).upper()
        fuel = risk.get("primary_fuel", "")
        ts = msg.get("ts", time.time())
        tstr = time.strftime("%H:%M:%S", time.localtime(ts))
        coord = (f"{lat:.5f}, {lon:.5f}"
                 if lat is not None and lon is not None else "--")
        text = f"[{tstr}]  FIRE @ {coord}   {level}"
        if fuel:
            text += f" ({TERRAIN_LABELS.get(fuel, fuel)})"
        item = QListWidgetItem(text)
        item.setForeground(QColor(RISK_COLORS.get(level, FG)))
        self.log.insertItem(0, item)


# --------------------------------------------------------------------------
# Standalone demo: `python detection_panel.py` to see it with fake data.
# Walks the full PATROL -> detect -> climb -> analyze(10s) -> event -> resume
# cycle on a loop so you can judge the layout and the information shown.
# --------------------------------------------------------------------------
def _demo():
    app = QApplication(sys.argv)
    panel = DetectionPanel()
    panel.setWindowTitle("ANKEBUT - Detection panel (demo)")
    panel.resize(360, 780)
    panel.show()

    step = {"n": 0}

    def tick():
        n = step["n"]
        lat = 41.0200 + n * 0.00001
        lon = 28.9800 + n * 0.00001
        drone = {"lat": lat, "lon": lon, "alt_m": 45.0, "heading_deg": 270}
        phase = n % 40

        if phase < 20:
            panel.update_detection({"type": "detection", "ts": time.time(),
                                    "state": "PATROL", "detections": [],
                                    "drone": drone})
        elif phase == 20:
            panel.update_detection({"type": "detection", "ts": time.time(),
                                    "state": "LOCKED", "drone": drone,
                                    "detections": [
                                        {"label": "fire", "confidence": 0.92},
                                        {"label": "smoke", "confidence": 0.74}]})
        elif phase == 21:
            panel.update_detection({"type": "detection", "ts": time.time(),
                                    "state": "CLIMBING", "drone": drone,
                                    "detections": [
                                        {"label": "fire", "confidence": 0.90}]})
        elif 22 <= phase <= 31:
            panel.update_detection({"type": "detection", "ts": time.time(),
                                    "state": "ANALYZING",
                                    "analyze_remaining_s": 31 - phase + 1,
                                    "drone": drone,
                                    "detections": [
                                        {"label": "fire", "confidence": 0.93}]})
        elif phase == 32:
            panel.register_event({
                "type": "fire_event", "ts": time.time(), "event_id": f"evt_{n}",
                "fire_location": {"lat": lat, "lon": lon, "alt_m": 50.0},
                "terrain": {"tree": 0.10, "green_grass": 0.10, "dry_grass": 0.60,
                            "water": 0.05, "soil": 0.15},
                "wind": {"direction": "N", "direction_deg": 0, "speed_ms": 4.2},
                "risk": {"level": "HIGH", "primary_fuel": "dry_grass",
                         "note": "Dry grass dominates with a northerly wind - "
                                 "fast spread likely toward the tree line."}})
            panel.update_detection({"type": "detection", "ts": time.time(),
                                    "state": "DESCENDING", "drone": drone,
                                    "detections": [
                                        {"label": "fire", "confidence": 0.91}]})
        elif phase == 33:
            panel.update_detection({"type": "detection", "ts": time.time(),
                                    "state": "RESUMING", "detections": [],
                                    "drone": drone})
        else:
            panel.update_detection({"type": "detection", "ts": time.time(),
                                    "state": "PATROL", "detections": [],
                                    "drone": drone})

        step["n"] += 1

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(1000)  # 1 Hz for a readable demo (real feed would be ~10 Hz)

    sys.exit(app.exec())


if __name__ == "__main__":
    _demo()