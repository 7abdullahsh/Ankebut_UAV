"""Find which DeepLabV3 class index = which terrain type.

The training notebook proves there are 6 classes (indices 0-5) but does NOT
record which name maps to which index - that came from CVAT's per-label colours
(sorted into indices), which aren't in the notebook. So we identify them by
looking at where each index actually lands on an image you recognise.

  python seg_class_finder.py path/to/known_image.jpg

Best image: a validation frame where you can clearly see water AND trees AND
grass AND soil. For each class index present it prints the coverage % and saves

  class_<i>.png  =  the image with ONLY that index highlighted in red.

Open them (or scp to your laptop), note which index covers water, which covers
trees, etc., then tell me the mapping and I set SEG_CLASS_MAP.
"""

import sys

import cv2
import numpy as np

import config
from segmenter import Segmenter

if len(sys.argv) < 2:
    print("usage: python seg_class_finder.py <image_path>")
    sys.exit(1)

frame = cv2.imread(sys.argv[1])
if frame is None:
    print("could not read image:", sys.argv[1])
    sys.exit(1)

seg = Segmenter(config.SEG_MODEL_PATH, num_classes=config.SEG_NUM_CLASSES,
                input_size=config.SEG_INPUT_SIZE)
seg.start()

mask = seg._predict_mask(frame)                       # raw class indices (512x512)
mask_full = cv2.resize(mask, (frame.shape[1], frame.shape[0]),
                       interpolation=cv2.INTER_NEAREST)

total = mask_full.size
print("\nclass index coverage on this image:")
for idx in range(config.SEG_NUM_CLASSES):
    cnt = int(np.count_nonzero(mask_full == idx))
    pct = 100.0 * cnt / total
    print(f"   index {idx}: {pct:5.1f}%")
    if cnt == 0:
        continue
    highlight = np.zeros_like(frame)
    highlight[mask_full == idx] = (0, 0, 255)         # red where this index is
    vis = cv2.addWeighted(frame, 0.5, highlight, 0.5, 0)
    cv2.imwrite(f"class_{idx}.png", vis)

print("\nsaved class_<i>.png for each present index.")
print("Open each and identify it, then report e.g.:")
print("   0=background, 1=?, 2=?, 3=?, 4=?, 5=?")
