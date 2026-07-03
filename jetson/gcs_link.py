"""ANKEBUT Jetson - GCS data link.

Sends the JSON messages to the ground station over UDP. This is the DATA
pipe, separate from the annotated-video pipe.

The schema here MUST stay in sync with the GCS detection panel
(ui/detection_panel.py) - the ground station parses exactly these fields:

  detection  -> update_detection()
  fire_event -> register_event()

GcsLink takes host/port as arguments (it does not import config) so it can be
reused and unit-tested on its own; main.py passes the config values in.
"""

import json
import socket
import threading
import time


class GcsLink:
    def __init__(self, host, port):
        self.host = host
        self.port = int(port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._lock = threading.Lock()
        self._event_counter = 0

    # ------------------------------------------------------------------
    def _send(self, payload):
        """JSON-encode and fire one UDP datagram. Never blocks meaningfully."""
        data = json.dumps(payload).encode("utf-8")
        with self._lock:
            self._sock.sendto(data, (self.host, self.port))

    # ------------------------------------------------------------------
    @staticmethod
    def detection(label, confidence):
        """Build one detection entry: {'label': ..., 'confidence': ...}."""
        return {"label": str(label), "confidence": float(confidence)}

    # ------------------------------------------------------------------
    def send_detection(self, state, detections=None, drone=None,
                       analyze_remaining_s=None):
        """High-frequency live detection message (~10 Hz while patrolling).

        state                 : "PATROL"|"LOCKED"|"CLIMBING"|"ANALYZING"|
                                "DESCENDING"|"RESUMING"
        detections            : list of detection() dicts (may be empty)
        drone                 : {"lat":.., "lon":.., "alt_m":.., "heading_deg":..}
        analyze_remaining_s   : seconds left in the analysis (only during ANALYZING)
        """
        msg = {
            "type": "detection",
            "ts": time.time(),
            "state": state,
            "detections": detections or [],
            "drone": drone or {},
        }
        if analyze_remaining_s is not None:
            msg["analyze_remaining_s"] = int(analyze_remaining_s)
        self._send(msg)
        return msg

    # ------------------------------------------------------------------
    def send_fire_event(self, fire_location, terrain=None, wind=None,
                       risk=None):
        """One-shot rich packet emitted once the analysis for a fire finishes.

        fire_location : {"lat":.., "lon":.., "alt_m":..}      (the locked GPS)
        terrain       : {"tree":0..1, "green_grass":.., "dry_grass":..,
                         "water":.., "soil":..}                (segmentation)
        wind          : {"direction":"N", "direction_deg":0, "speed_ms":4.2}
        risk          : {"level":"HIGH", "primary_fuel":"dry_grass", "note":".."}
        """
        self._event_counter += 1
        msg = {
            "type": "fire_event",
            "ts": time.time(),
            "event_id": f"evt_{self._event_counter:04d}",
            "fire_location": fire_location or {},
            "terrain": terrain or {},
            "wind": wind or {},
            "risk": risk or {},
        }
        self._send(msg)
        return msg

    # ------------------------------------------------------------------
    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# Quick self-check: `python gcs_link.py` sends one of each message to a local
# listener and prints what came across the wire.
# --------------------------------------------------------------------------
if __name__ == "__main__":
    port = 14599
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", port))
    rx.settimeout(2.0)

    link = GcsLink("127.0.0.1", port)
    link.send_detection(
        "ANALYZING",
        detections=[GcsLink.detection("fire", 0.92),
                    GcsLink.detection("smoke", 0.71)],
        drone={"lat": 41.0200, "lon": 28.9800, "alt_m": 45.0, "heading_deg": 270},
        analyze_remaining_s=7,
    )
    link.send_fire_event(
        fire_location={"lat": 41.0213, "lon": 28.9841, "alt_m": 50.0},
        terrain={"tree": 0.10, "green_grass": 0.10, "dry_grass": 0.60,
                 "water": 0.05, "soil": 0.15},
        wind={"direction": "N", "direction_deg": 0, "speed_ms": 4.2},
        risk={"level": "HIGH", "primary_fuel": "dry_grass", "note": "demo"},
    )

    for _ in range(2):
        data, _addr = rx.recvfrom(65535)
        print("received:", json.loads(data)["type"], "->", data.decode())
    rx.close()
    link.close()
