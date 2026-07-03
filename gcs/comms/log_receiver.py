"""ANKEBUT GCS - log / telemetry receiver.

A QThread that listens on the JSON UDP port (config.AI_RESULTS_UDP_PORT,
14560) for the messages the Jetson sends via gcs_link.py, and re-emits them as
Qt signals so the GUI thread updates the detection panel without ever
blocking (cross-thread signals are delivered on the GUI thread automatically).

This is the missing half of the data pipe: the video already arrives on its
own port; this brings in the detections, locations and fire events.

Wire-up (already done in main_window.py):
    self.log_rx = LogReceiver(config.AI_RESULTS_UDP_PORT)
    self.log_rx.detection_received.connect(self.detection_panel.update_detection)
    self.log_rx.fire_event_received.connect(self.detection_panel.register_event)
    self.log_rx.start()
"""

import json
import socket

from PySide6.QtCore import QThread, Signal


class LogReceiver(QThread):
    detection_received = Signal(dict)
    fire_event_received = Signal(dict)
    message_received = Signal(dict)    # raw, for status bar / debugging

    def __init__(self, port, host="0.0.0.0"):
        super().__init__()
        self.port = int(port)
        self.host = host
        self._running = False

    def run(self):
        self._running = True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError as e:
            print("[LOG] bind failed:", e)
            return
        sock.settimeout(0.5)        # so the loop can notice _running == False
        print(f"[LOG] listening on UDP {self.port}")

        while self._running:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue                # ignore malformed packets

            self.message_received.emit(msg)
            mtype = msg.get("type")
            if mtype == "detection":
                self.detection_received.emit(msg)
            elif mtype == "fire_event":
                self.fire_event_received.emit(msg)

        sock.close()
        print("[LOG] stopped")

    def stop(self):
        self._running = False
        self.wait(2000)
