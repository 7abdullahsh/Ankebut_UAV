"""Goruntu isleme yokken kullanmak icin sahte AI sonuc yayicisi.
Random veri uretip JSON olarak UDP'ye yollar."""
import json
import math
import os
import random
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

PORT = 14560

DIRECTIONS = [
    (0, "Kuzey"), (45, "Kuzeydogu"), (90, "Dogu"), (135, "Guneydogu"),
    (180, "Guney"), (225, "Guneybati"), (270, "Bati"), (315, "Kuzeybati"),
]


def main() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"AI sonuc simulatoru -> udp://{config.TARGET_HOST}:{PORT}")
    print("Ctrl+C ile durdur.\n")
    
    t = 0.0
    try:
        while True:
            # Once 5 saniye normal, sonra alarm modu
            alarm = (int(t) // 10) % 2 == 1
            
            angle = int((t * 10) % 360)
            d_angle, d_text = min(DIRECTIONS, key=lambda x: abs(x[0] - angle) % 360)
            
            msg = {
                "type": "detection",
                "target_lat": 41.0370 + math.sin(t * 0.1) * 0.0005,
                "target_lon": 28.9857 + math.cos(t * 0.1) * 0.0005,
                "distance_m": int(50 + 10 * math.sin(t * 0.2)),
                "spread_angle": angle,
                "spread_text": d_text,
                "confidence": int(85 + 14 * random.random()),
                "alarm": alarm,
            }
            
            sock.sendto(json.dumps(msg).encode('utf-8'),
                       (config.TARGET_HOST, PORT))
            t += 1.0
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("Durduruldu.")
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())