"""TEST (Step 1 & 3) - send simulated Jetson messages to the running GCS.

Self-contained (no project imports), so it runs on the laptop OR the Jetson.
Walk the full PATROL -> fire -> analyze -> event -> resume cycle over real UDP
while the GCS app is running, and watch the detection panel react.

  Step 1 (laptop):  python test_send_to_gcs.py
  Step 3 (Jetson):  python test_send_to_gcs.py 192.168.4.9   <- your GCS IP
"""

import json
import socket
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = 14560                      # GCS JSON port (AI_RESULTS_UDP_PORT)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send(d):
    sock.sendto(json.dumps(d).encode("utf-8"), (HOST, PORT))


lat, lon = 41.0200, 28.9800
print(f"sending to {HOST}:{PORT}  (Ctrl+C to stop)")
try:
    while True:
        print("  PATROL (no fire)...")
        for _ in range(8):
            send({"type": "detection", "state": "PATROL", "detections": [],
                  "drone": {"lat": lat, "lon": lon, "alt_m": 45, "heading_deg": 270}})
            time.sleep(0.5)

        print("  FIRE detected")
        send({"type": "detection", "state": "LOCKED",
              "detections": [{"label": "fire", "confidence": 0.92},
                             {"label": "smoke", "confidence": 0.70}],
              "drone": {"lat": lat, "lon": lon, "alt_m": 45}})
        time.sleep(1)

        print("  ANALYZING (10s)")
        for r in range(10, 0, -1):
            send({"type": "detection", "state": "ANALYZING", "analyze_remaining_s": r,
                  "detections": [{"label": "fire", "confidence": 0.93}],
                  "drone": {"lat": lat, "lon": lon, "alt_m": 50}})
            time.sleep(1)

        print("  FIRE EVENT")
        send({"type": "fire_event",
              "fire_location": {"lat": lat, "lon": lon, "alt_m": 50},
              "terrain": {"tree": 0.10, "green_grass": 0.10, "dry_grass": 0.60,
                          "water": 0.05, "soil": 0.15},
              "wind": {"direction": "N", "direction_deg": 0, "speed_ms": 4.2},
              "risk": {"level": "HIGH", "primary_fuel": "dry_grass",
                       "note": "dry grass + N wind"}})

        send({"type": "detection", "state": "RESUMING", "detections": [],
              "drone": {"lat": lat, "lon": lon, "alt_m": 45}})
        time.sleep(2)
except KeyboardInterrupt:
    print("\nstopped")
