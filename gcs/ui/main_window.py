"""ANKEBUT GCS - Main window and layout."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QSplitter, QStatusBar, QLabel,
    QScrollArea, QFrame
)
import config
from ui.video_widget import VideoPanel
from ui.mission_planner_host import MissionPlannerHost
from ui.detection_panel import DetectionPanel
from comms.log_receiver import LogReceiver


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.WINDOW_TITLE)
        self.resize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)
        self._build_ui()
        self._build_status_bar()
        self._start_log_receiver()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Left (Mission Planner)  |  Right (Video on top, Detection panel below)
        top_splitter = QSplitter(Qt.Horizontal)

        # Left: Mission Planner (full height)
        self.mp_host = MissionPlannerHost(
            title_match=config.MISSION_PLANNER_TITLE_MATCH
        )

        # Right: vertical splitter so the video and the detection panel
        # can be resized against each other by dragging.
        right_splitter = QSplitter(Qt.Vertical)

        self.video_panel = VideoPanel(udp_port=config.VIDEO_UDP_PORT)
        self.detection_panel = DetectionPanel()

        # The detection panel has a lot of content. Put it in a scroll area so
        # its top (the state badge + alert banner) is ALWAYS fully visible no
        # matter how the panes are sized - if the pane gets short, it scrolls
        # instead of clipping the boxes at the top.
        det_scroll = QScrollArea()
        det_scroll.setWidgetResizable(True)
        det_scroll.setWidget(self.detection_panel)
        det_scroll.setFrameShape(QFrame.NoFrame)
        det_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        det_scroll.setStyleSheet("background-color: #1F3346;")

        right_splitter.addWidget(self.video_panel)
        right_splitter.addWidget(det_scroll)
        # Give the detection panel the larger share, and let it absorb extra
        # height when the window grows (so the boxes never get squeezed).
        right_splitter.setSizes([360, 600])
        right_splitter.setStretchFactor(0, 0)
        right_splitter.setStretchFactor(1, 1)

        top_splitter.addWidget(self.mp_host)
        top_splitter.addWidget(right_splitter)
        top_splitter.setSizes([920, 1000])

        main_layout.addWidget(top_splitter, stretch=1)

    def _build_status_bar(self):
        status = QStatusBar()
        self.cmd_label = QLabel("System ready")
        self.cmd_label.setStyleSheet("color: white; padding: 0 10px;")
        status.addWidget(self.cmd_label)
        self.setStatusBar(status)

        # Reflect the live video link state in the status bar.
        self.video_panel.connection_changed.connect(self._on_video_state)

    def _start_log_receiver(self):
        # Listens on the JSON port for the Jetson's detection / fire_event
        # messages and drives the detection panel (runs off the GUI thread).
        self.log_rx = LogReceiver(config.AI_RESULTS_UDP_PORT)
        self.log_rx.detection_received.connect(self.detection_panel.update_detection)
        self.log_rx.fire_event_received.connect(self.detection_panel.register_event)
        self.log_rx.message_received.connect(self._on_jetson_message)
        self.log_rx.start()

    def _on_jetson_message(self, msg):
        if msg.get("type") == "fire_event":
            self.cmd_label.setText("Jetson: FIRE EVENT logged")
        elif msg.get("type") == "detection":
            self.cmd_label.setText(f"Jetson: {msg.get('state', '--')}")

    def _on_video_state(self, state):
        labels = {
            "ok": "Video: live",
            "warn": "Video: connecting...",
            "err": "Video: connection lost",
            "off": "Video: stopped",
        }
        self.cmd_label.setText(labels.get(state, "Video: --"))

    def closeEvent(self, event):
        # Stop background threads cleanly on exit.
        try:
            self.log_rx.stop()
        except Exception:
            pass
        try:
            self.video_panel.shutdown()
        except Exception:
            pass
        super().closeEvent(event)
