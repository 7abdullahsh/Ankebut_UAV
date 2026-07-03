"""TEST - YOLO fire/smoke detector. Run on the Jetson, in the main/ folder.

  python test_detector.py path/to/fire_image.jpg    # best: a known fire image
  python test_detector.py                           # else: one camera frame

Prints the detections and saves an annotated image (det_out.jpg) you can open
or scp to your laptop. It also prints the model's RAW class names so you can
confirm the mapping - the model says "fire"/"tree", and your config turns
"tree" into "smoke".
"""

import sys

import cv2

import config
from fire_detector import FireDetector


def get_frame():
    if len(sys.argv) > 1:
        f = cv2.imread(sys.argv[1])
        if f is None:
            print("could not read image:", sys.argv[1])
            sys.exit(1)
        return f
    from camera import Camera
    import time
    cam = Camera(config.CAMERA_INDEX, config.FRAME_WIDTH,
                 config.FRAME_HEIGHT, config.FRAME_FPS)
    cam.start()
    time.sleep(1.0)
    f = cam.read()
    cam.stop()
    if f is None:
        print("no camera frame")
        sys.exit(1)
    return f


det = FireDetector(config.YOLO_MODEL_PATH,
                   conf_threshold=config.FIRE_CONF_THRESHOLD,
                   fire_labels=config.FIRE_LABELS,
                   smoke_labels=config.SMOKE_LABELS)
det.start()
print("model raw class names:", det._model.names)
print("config FIRE_LABELS =", config.FIRE_LABELS,
      " SMOKE_LABELS =", config.SMOKE_LABELS)

frame = get_frame()
dets, annotated = det.detect(frame)

print(f"\n{len(dets)} detection(s):")
for d in dets:
    print("   ", d)
if not dets:
    print("   (none - if you used the live camera with no fire in view,")
    print("    that is expected. Try a real fire image as an argument.)")

cv2.imwrite("det_out.jpg", annotated)
print("\nsaved det_out.jpg (annotated)")
