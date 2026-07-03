"""ANKEBUT GCS - Control buttons panel."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QMessageBox, QFrame
)


class ControlButton(QPushButton):
    """Standard-styled control button (with color level)."""

    STYLES = {
        "danger":  {"bg": "#C0392B", "hover": "#E74C3C"},
        "warning": {"bg": "#D68910", "hover": "#F39C12"},
        "safe":    {"bg": "#229954", "hover": "#27AE60"},
        "neutral": {"bg": "#34495E", "hover": "#4A6278"},
    }

    def __init__(self, text, style="neutral"):
        super().__init__(text)
        self.setMinimumHeight(40)
        self.setMinimumWidth(120)
        s = self.STYLES.get(style, self.STYLES["neutral"])
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {s['bg']};
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {s['hover']};
            }}
            QPushButton:pressed {{
                background-color: {s['bg']};
                padding-top: 2px;
            }}
        """)


class ControlBar(QWidget):
    """Manual command buttons.

    In Phase 4 these buttons will send real MAVLink commands;
    for now they only emit signals and log to the console.
    """

    arm_requested = Signal()
    disarm_requested = Signal()
    takeoff_requested = Signal()
    land_requested = Signal()
    rtl_requested = Signal()
    mission_start_requested = Signal()
    mission_stop_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setStyleSheet("background-color: #2B3D4F;")
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.btn_arm = ControlButton("ARM", "danger")
        self.btn_arm.clicked.connect(self._on_arm)

        self.btn_disarm = ControlButton("DISARM", "neutral")
        self.btn_disarm.clicked.connect(self._on_disarm)

        self.btn_takeoff = ControlButton("TAKEOFF", "warning")
        self.btn_takeoff.clicked.connect(self._on_takeoff)

        self.btn_land = ControlButton("LAND", "warning")
        self.btn_land.clicked.connect(self._on_land)

        self.btn_rtl = ControlButton("RTL", "warning")
        self.btn_rtl.clicked.connect(self._on_rtl)

        self.btn_mission_start = ControlButton("MISSION START", "safe")
        self.btn_mission_start.clicked.connect(self._on_mission_start)

        self.btn_mission_stop = ControlButton("MISSION STOP", "neutral")
        self.btn_mission_stop.clicked.connect(self._on_mission_stop)

        layout.addWidget(self.btn_arm)
        layout.addWidget(self.btn_disarm)
        layout.addWidget(self._make_separator())
        layout.addWidget(self.btn_takeoff)
        layout.addWidget(self.btn_land)
        layout.addWidget(self.btn_rtl)
        layout.addWidget(self._make_separator())
        layout.addWidget(self.btn_mission_start)
        layout.addWidget(self.btn_mission_stop)
        layout.addStretch()

    def _make_separator(self):
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet("color: #3A5066; background-color: #3A5066;")
        sep.setFixedWidth(1)
        return sep

    def _confirm(self, title, message):
        """Confirmation dialog for dangerous commands."""
        reply = QMessageBox.warning(
            self, title, message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No  # Default NO (protection against accidental Enter)
        )
        return reply == QMessageBox.Yes

    def _on_arm(self):
        if self._confirm("ARM Confirmation",
                         "The drone will be ARMED (motors will be armed).\n\nAre you sure you want to continue?"):
            print("[COMMAND] ARM sent")
            self.arm_requested.emit()

    def _on_disarm(self):
        print("[COMMAND] DISARM sent")
        self.disarm_requested.emit()

    def _on_takeoff(self):
        if self._confirm("TAKEOFF Confirmation",
                         "The drone will take off automatically.\n\nAre you sure you want to continue?"):
            print("[COMMAND] TAKEOFF sent")
            self.takeoff_requested.emit()

    def _on_land(self):
        print("[COMMAND] LAND sent")
        self.land_requested.emit()

    def _on_rtl(self):
        if self._confirm("RTL Confirmation",
                         "The drone will switch to Return-To-Launch mode (return home).\n\nDo you confirm?"):
            print("[COMMAND] RTL sent")
            self.rtl_requested.emit()

    def _on_mission_start(self):
        print("[COMMAND] MISSION START sent")
        self.mission_start_requested.emit()

    def _on_mission_stop(self):
        print("[COMMAND] MISSION STOP sent")
        self.mission_stop_requested.emit()
