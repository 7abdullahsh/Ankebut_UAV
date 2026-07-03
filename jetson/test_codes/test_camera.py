"""TEST (Step 4) - camera.py in isolation. Run on the Jetson.

Opens the camera through camera.py, reads for 5 s, reports the frame size and
measured FPS. No GCS, no models - just proves the camera path.

  python test_camera.py
"""

import time

import config
from camera import Camera

cam = Camera(source=config.CAMERA_INDEX,
             width=config.FRAME_WIDTH, height=config.FRAME_HEIGHT,
             fps=config.FRAME_FPS)
cam.start()
print("warming up...")
time.sleep(1.0)

t0 = time.time()
n = 0
shape = None
while time.time() - t0 < 5.0:
    f = cam.read()
    if f is not None:
        n += 1
        shape = f.shape
    time.sleep(0.005)
cam.stop()

if shape is None:
    print("NO FRAMES - camera not delivering. Check CAMERA_INDEX and cabling.")
else:
    print(f"OK: frame {shape[1]}x{shape[0]}, ~{n / 5:.0f} FPS over 5 s")
