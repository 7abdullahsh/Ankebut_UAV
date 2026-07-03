"""ANKEBUT GCS - Telemetry panel."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar
)


class TelemetryRow(QWidget):
    """Single-line telemetry item: label + value + unit."""
    def __init__(self, label, unit=""):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.label = QLabel(label)
        self.label.setStyleSheet("color: #8FA8C2; font-size: 12px;")
        self.label.setMinimumWidth(110)

        self.value = QLabel("---")
        self.value.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.unit = QLabel(unit)
        self.unit.setStyleSheet("color: #8FA8C2; font-size: 11px;")
        self.unit.setMinimumWidth(35)

        layout.addWidget(self.label)
        layout.addStretch()
        layout.addWidget(self.value)
        layout.addWidget(self.unit)

    def set_value(self, text):
        self.value.setText(str(text))


class SectionHeader(QLabel):
    """Section header with bottom underline."""
    def __init__(self, text):
        super().__init__(text)
        self.setStyleSheet("""
            color: #8FA8C2;
            font-size: 11px;
            font-weight: bold;
            border-bottom: 1px solid #3A5066;
            padding: 4px 0;
            margin-top: 8px;
        """)


class TelemetryPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #1F3346;")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)

        title = QLabel("TELEMETRY")
        title.setStyleSheet("color: white; font-size: 14px; font-weight: bold; padding: 4px;")
        layout.addWidget(title)

        # ---- STATUS ----
        layout.addWidget(SectionHeader("STATUS"))
        self.row_mode = TelemetryRow("Flight Mode")
        self.row_armed = TelemetryRow("ARM Status")
        layout.addWidget(self.row_mode)
        layout.addWidget(self.row_armed)

        # ---- BATTERY ----
        layout.addWidget(SectionHeader("BATTERY"))
        self.battery_bar = QProgressBar()
        self.battery_bar.setRange(0, 100)
        self.battery_bar.setValue(0)
        self.battery_bar.setFormat("%p%")
        self._set_battery_style("#27AE60")
        layout.addWidget(self.battery_bar)

        self.row_voltage = TelemetryRow("Voltage", "V")
        layout.addWidget(self.row_voltage)

        # ---- FLIGHT ----
        layout.addWidget(SectionHeader("FLIGHT"))
        self.row_altitude = TelemetryRow("Altitude", "m")
        self.row_speed = TelemetryRow("Ground Speed", "m/s")
        self.row_heading = TelemetryRow("Heading", "deg")
        layout.addWidget(self.row_altitude)
        layout.addWidget(self.row_speed)
        layout.addWidget(self.row_heading)

        # ---- GPS ----
        layout.addWidget(SectionHeader("GPS"))
        self.row_lat = TelemetryRow("Latitude")
        self.row_lon = TelemetryRow("Longitude")
        self.row_sats = TelemetryRow("Satellites")
        self.row_fix = TelemetryRow("Fix Type")
        layout.addWidget(self.row_lat)
        layout.addWidget(self.row_lon)
        layout.addWidget(self.row_sats)
        layout.addWidget(self.row_fix)

        # ---- CONNECTION ----
        layout.addWidget(SectionHeader("CONNECTION"))
        self.row_link = TelemetryRow("Telemetry")
        self.row_link.set_value("Waiting")
        layout.addWidget(self.row_link)

        layout.addStretch()

    def _set_battery_style(self, color):
        self.battery_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: #0F1A24;
                border: 1px solid #3A5066;
                border-radius: 3px;
                color: white;
                text-align: center;
                font-weight: bold;
                height: 22px;
            }}
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 2px;
            }}
        """)

    def update_telemetry(self, data):
        """Render the dict coming from TelemetryProvider to the screen."""
        self.row_mode.set_value(data["flight_mode"])
        self.row_armed.set_value("ARMED" if data["armed"] else "DISARMED")

        bp = int(data["battery_percent"])
        self.battery_bar.setValue(bp)
        if bp > 50:
            self._set_battery_style("#27AE60")  # green
        elif bp > 20:
            self._set_battery_style("#F39C12")  # orange
        else:
            self._set_battery_style("#E74C3C")  # red

        self.row_voltage.set_value(f"{data['battery_voltage']:.2f}")
        self.row_altitude.set_value(f"{data['altitude_m']:.1f}")
        self.row_speed.set_value(f"{data['ground_speed_ms']:.1f}")
        self.row_heading.set_value(f"{data['heading_deg']:.0f}")
        self.row_lat.set_value(f"{data['lat']:.6f}")
        self.row_lon.set_value(f"{data['lon']:.6f}")
        self.row_sats.set_value(str(data['satellites']))
        self.row_fix.set_value(data['gps_fix'])
        self.row_link.set_value("Active")
