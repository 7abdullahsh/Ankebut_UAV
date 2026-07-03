"""ANKEBUT GCS - Video panel (UDP MPEG-TS H.264 receiver).

Receives the MPEG-TS over UDP H.264 stream coming from the GStreamer
sender via OpenCV/FFmpeg and displays it on a PySide6 QLabel.

Sender example:
gst-launch-1.0 mfvideosrc device-index=1 ! image/jpeg,width=1280,height=720,framerate=30/1 ! jpegdec ! videoconvert ! x264enc tune=zerolatency bitrate=2000 speed-preset=ultrafast key-int-max=15 ! h264parse ! mpegtsmux ! udpsink host=YOUR_PC_IP port=5601 sync=false
"""

import os
import time

import cv2

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton
)


class StatusLED(QLabel):
    COLORS = {
        "ok": "#27AE60",
        "warn": "#F39C12",
        "err": "#E74C3C",
        "off": "#555555",
    }

    def __init__(self, status="off"):
        super().__init__()
        self.setFixedSize(14, 14)
        self.set_status(status)

    def set_status(self, status):
        color = self.COLORS.get(status, self.COLORS["off"])
        self.setStyleSheet(f"""
            background-color: {color};
            border-radius: 7px;
            border: 1px solid #222;
        """)


class UdpVideoReceiver(QThread):
    """Reads the UDP MPEG-TS H.264 stream in a separate thread."""

    frame_received = Signal(object)
    connection_lost = Signal()
    connection_restored = Signal()

    FRAME_TIMEOUT_SEC = 1.5

    def __init__(self, udp_port=5601):
        super().__init__()
        self.udp_port = udp_port
        self._running = False

    def _open_capture(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "fflags;nobuffer"
            "|flags;low_delay"
            "|probesize;32768"
            "|analyzeduration;0"
            # Socket timeout in microseconds (2 s). Without this, FFmpeg waits
            # its 30 s default for data, which is what made a dead stream stay
            # frozen for ages and made shutdown hang. With it, open/read give
            # up after ~2 s so the loop stays responsive and the view can go
            # black promptly.
            "|timeout;2000000"
        )

        url = (
            f"udp://@0.0.0.0:{self.udp_port}"
            "?fifo_size=50000000"
            "&overrun_nonfatal=1"
        )

        print(f"[VIDEO] Opening UDP MPEG-TS: {url}")

        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)

        print("[VIDEO] cap.isOpened():", cap.isOpened())

        return cap

    def run(self):
        self._running = True
        cap = None
        last_frame_time = time.time()
        connected = False
        first_frame_printed = False

        print(f"[VIDEO] Receiver started. UDP port: {self.udp_port}")

        while self._running:
            if cap is None or not cap.isOpened():
                cap = self._open_capture()

                if not cap.isOpened():
                    self.msleep(500)
                    continue

            # NOTE: do NOT reset last_frame_time here. Resetting it every
            # iteration is what used to keep the timeout from ever firing,
            # so a dead stream froze on the last frame instead of going
            # black. It is only updated below when a real frame arrives.
            ret, frame = cap.read()
            now = time.time()

            if ret and frame is not None:
                if not first_frame_printed:
                    print("[VIDEO] First frame received:", frame.shape)
                    first_frame_printed = True

                if not connected:
                    connected = True
                    self.connection_restored.emit()
                    print("[VIDEO] Connection established.")

                last_frame_time = now
                self.frame_received.emit(frame)

            else:
                if now - last_frame_time > self.FRAME_TIMEOUT_SEC:
                    if connected:
                        connected = False
                        self.connection_lost.emit()
                        print("[VIDEO] Connection lost.")

                    if cap is not None:
                        cap.release()

                    cap = None
                    first_frame_printed = False
                else:
                    self.msleep(10)

        if cap is not None:
            cap.release()

        print("[VIDEO] Receiver stopped.")

    def stop(self):
        self._running = False
        # The capture uses an FFmpeg "timeout" option (see _open_capture), so
        # a missing stream makes open/read return within ~2 s instead of
        # blocking forever. The loop then notices _running and exits on its
        # own, so we just wait for it. We deliberately do NOT call terminate():
        # killing the thread while it is inside OpenCV's C++ open crashes the
        # whole process.
        self.wait(4000)


class VideoPanel(QWidget):
    """Panel that receives the drone camera over UDP and displays it.

    Adds manual Stop / Reconnect / Snapshot controls and a small
    FPS + resolution readout. When the stream drops (or is stopped by
    the operator), the view goes black instead of freezing on the last
    frame.
    """

    connection_changed = Signal(str)

    def __init__(self, udp_port=5601):
        super().__init__()
        self.udp_port = udp_port
        self._current_state = "off"
        self._last_frame = None
        self._stopped = False
        self._fps = 0.0
        self._fps_count = 0
        self._fps_t0 = time.time()
        self._build_ui()
        self._start_receiver()

    def _make_button(self, text, bg, hover):
        btn = QPushButton(text)
        btn.setFixedHeight(24)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {bg}; color: white; "
            f"border: none; border-radius: 3px; padding: 2px 12px; "
            f"font-size: 11px; font-weight: bold; }} "
            f"QPushButton:hover {{ background-color: {hover}; }} "
            f"QPushButton:disabled {{ background-color: #2A3744; color: #5A6B7A; }}"
        )
        return btn

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- Header ----
        header = QFrame()
        header.setStyleSheet(
            "background-color: #1F3346; border: 1px solid #3A5066;"
        )
        header.setFixedHeight(32)

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 4, 10, 4)

        title = QLabel(f"DRONE CAMERA")
        title.setStyleSheet(
            "color: white; font-weight: bold; font-size: 13px;"
        )

        self.led = StatusLED("warn")

        self.status_label = QLabel("Waiting for connection...")
        self.status_label.setStyleSheet(
            "color: white; font-size: 12px; font-weight: bold;"
        )

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(self.led)
        header_layout.addWidget(self.status_label)

        # ---- Controls ----
        controls = QFrame()
        controls.setStyleSheet("background-color: #18293A;")
        controls.setFixedHeight(38)

        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(10, 6, 10, 6)
        controls_layout.setSpacing(8)

        self.stop_btn = self._make_button("Stop", "#C0392B", "#E74C3C")
        self.stop_btn.clicked.connect(self._on_stop)

        self.reconnect_btn = self._make_button("Reconnect", "#229954", "#27AE60")
        self.reconnect_btn.clicked.connect(self._on_reconnect)

        self.snapshot_btn = self._make_button("Snapshot", "#34495E", "#4A6278")
        self.snapshot_btn.clicked.connect(self._on_snapshot)
        self.snapshot_btn.setEnabled(False)

        controls_layout.addWidget(self.stop_btn)
        controls_layout.addWidget(self.reconnect_btn)
        controls_layout.addWidget(self.snapshot_btn)
        controls_layout.addStretch()

        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #6E869E; font-size: 11px;")
        controls_layout.addWidget(self.info_label)

        # ---- Video area ----
        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(480, 240)
        self.video_label.setStyleSheet(
            "background-color: black; color: white; font-size: 14px; font-weight: bold;"
        )
        self.video_label.setText("WAITING FOR VIDEO...")

        layout.addWidget(header)
        layout.addWidget(controls)
        layout.addWidget(self.video_label, stretch=1)

    # ---- Receiver lifecycle ----
    def _start_receiver(self):
        self._stopped = False
        self.receiver = UdpVideoReceiver(udp_port=self.udp_port)
        self.receiver.frame_received.connect(self._on_frame)
        self.receiver.connection_lost.connect(self._on_connection_lost)
        self.receiver.connection_restored.connect(self._on_connection_restored)
        self.receiver.start()

    def _stop_receiver(self):
        if getattr(self, "receiver", None) is not None:
            try:
                self.receiver.stop()
            except Exception as e:
                print("[VIDEO] Receiver stop error:", e)
            self.receiver = None

    # ---- Button handlers ----
    def _on_stop(self):
        self._stopped = True
        self._stop_receiver()
        self._last_frame = None
        self._fps = 0.0
        self.snapshot_btn.setEnabled(False)
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText("CAMERA STOPPED")
        self.info_label.setText("")
        self._set_state("off", "Stopped")
        print("[VIDEO] Camera stopped by user.")

    def _on_reconnect(self):
        self._stop_receiver()
        self._last_frame = None
        self.snapshot_btn.setEnabled(False)
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText("WAITING FOR VIDEO...")
        self.info_label.setText("")
        self._set_state("warn", "Connecting...")
        self._start_receiver()
        print("[VIDEO] Reconnecting...")

    def _on_snapshot(self):
        if self._last_frame is None:
            self.status_label.setText("No frame to save")
            return
        try:
            os.makedirs("snapshots", exist_ok=True)
            fname = time.strftime("snapshots/snapshot_%Y%m%d_%H%M%S.png")
            cv2.imwrite(fname, self._last_frame)
            self.status_label.setText(f"Saved: {fname}")
            print(f"[VIDEO] Snapshot saved: {fname}")
        except Exception as e:
            self.status_label.setText("Snapshot failed")
            print("[VIDEO] Snapshot error:", e)

    # ---- Stream callbacks ----
    def _on_frame(self, frame_bgr):
        try:
            self._last_frame = frame_bgr

            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape

            qimg = QImage(
                rgb.data, w, h, ch * w, QImage.Format_RGB888
            ).copy()
            pix = QPixmap.fromImage(qimg)
            scaled = pix.scaled(
                self.video_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.video_label.setPixmap(scaled)

            if self._current_state != "ok":
                self._set_state("ok", "Live stream")
                self.snapshot_btn.setEnabled(True)

            self._fps_count += 1
            elapsed = time.time() - self._fps_t0
            if elapsed >= 1.0:
                self._fps = self._fps_count / elapsed
                self._fps_count = 0
                self._fps_t0 = time.time()
                self.info_label.setText(f"{self._fps:.0f} FPS | {w}x{h}")

        except Exception as e:
            print("[VIDEO] Frame display error:", e)

    def _on_connection_lost(self):
        # Ignore the teardown signal that can fire while we are
        # intentionally stopping the camera.
        if self._stopped:
            return
        self._last_frame = None
        self.snapshot_btn.setEnabled(False)
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText("NO VIDEO\n\nIs the sender running?")
        self.info_label.setText("")
        self._set_state("err", "Connection lost")

    def _on_connection_restored(self):
        self._set_state("ok", "Reconnected")
        self.snapshot_btn.setEnabled(True)

    def _set_state(self, state, text):
        self._current_state = state
        self.led.set_status(state)
        self.status_label.setText(text)
        self.connection_changed.emit(state)

    def shutdown(self):
        self._stop_receiver()
