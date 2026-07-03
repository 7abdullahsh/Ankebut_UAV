"""TEST (Step 5) - camera.py + video_sender.py together. Run on the Jetson.

Opens the camera, draws a marker on each frame (to fake the AI annotation and
prove THIS pipeline rather than the old standalone script), and streams to the
GCS. Watch the GCS video panel: you should see the live feed with a red box
and a ticking timestamp.

Set config.GCS_HOST to your laptop IP first (e.g. 192.168.4.9).

  python test_camera_to_gcs.py
"""

import time

import cv2

import config
from camera import Camera
from video_sender import VideoSender

cam = Camera(config.CAMERA_INDEX, config.FRAME_WIDTH, config.FRAME_HEIGHT,
             config.FRAME_FPS)
vid = VideoSender(config.GCS_HOST, config.GCS_VIDEO_PORT,
                  config.FRAME_WIDTH, config.FRAME_HEIGHT, config.FRAME_FPS)

cam.start()
vid.start()
print(f"streaming to {config.GCS_HOST}:{config.GCS_VIDEO_PORT}  (Ctrl+C to stop)")

n = 0
try:
    while True:
        frame = cam.read()
        if frame is None:
            time.sleep(0.01)
            continue
        # fake "annotation" so you can confirm this is the new annotated path
        cv2.rectangle(frame, (20, 20), (200, 120), (0, 0, 255), 2)
        cv2.putText(frame, time.strftime("%H:%M:%S"), (25, 160),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        vid.send(frame)
        n += 1
        if n % 30 == 0:
            print(f"{n} frames sent")
except KeyboardInterrupt:
    print("\nstopping")
finally:
    cam.stop()
    vid.close()
