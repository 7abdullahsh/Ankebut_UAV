"""TEST - DeepLabV3 terrain segmenter. Run on the Jetson, in the main/ folder.

  python test_segmenter.py path/to/terrain_image.jpg   # best: a terrain image
  python test_segmenter.py                              # else: one camera frame

Prints the terrain composition (the percentages the GCS fire-event will carry)
and saves a colour overlay (seg_out.jpg) you can open or scp to confirm the
masks land on the right regions.

Loads torch + torchvision the first time - the first run is slow while the
model loads. If torch is not installed, install it for your Jetson first.
"""

import sys

import cv2

import config
from segmenter import Segmenter


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


seg = Segmenter(config.SEG_MODEL_PATH, num_classes=config.SEG_NUM_CLASSES,
                input_size=config.SEG_INPUT_SIZE, class_map=config.SEG_CLASS_MAP)
seg.start()

frame = get_frame()
terrain, annotated = seg.segment(frame)

print("\nterrain composition (what the GCS event will show):")
for k, v in terrain.items():
    bar = "#" * int(v * 30)
    print(f"   {k:12s} {v * 100:5.1f}%  {bar}")

cv2.imwrite("seg_out.jpg", annotated)
print("\nsaved seg_out.jpg (mask overlay)")
