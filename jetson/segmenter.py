"""ANKEBUT Jetson - terrain segmenter (DeepLabV3 + MobileNetV3-Large).

IMPORTANT: the trained checkpoint best_deeplabv3_v2.pth is a torchvision
**deeplabv3_mobilenet_v3_large** model - NOT resnet50 and NOT Ultralytics.
(The resnet50 training notebook is an earlier/different version.) We know it's
mobilenet_v3_large from the checkpoint itself: the classifier ASPP takes 960
input channels (mobilenet's high-level feature width), and the backbone uses
MobileNetV3 inverted-residual + squeeze-excite blocks.

  * model:        deeplabv3_mobilenet_v3_large, head = DeepLabHead(960, 6)
  * num_classes:  6  (background + 5 terrain classes)
  * forward:      model(x)["out"] -> [1, 6, H, W]; argmax over channels

Preprocessing (BGR->RGB, resize to SEG_INPUT_SIZE, /255, no ImageNet norm) is
assumed to match the team's Dataset pipeline. If the v2 model used different
preprocessing, that is the one thing to double-check against its training code.

Requires torch + torchvision (and numpy < 2 on the Jetson).
"""

import cv2
import numpy as np

TERRAIN_KEYS = ("tree", "green_grass", "dry_grass", "water", "soil")

DEFAULT_CLASS_MAP = {1: "tree", 2: "green_grass", 3: "dry_grass", 4: "soil", 5: "water"}

CLASS_COLORS = {
    1: (99, 110, 141),    # soil
    2: (39, 162, 201),    # dry_grass
    3: (66, 179, 124),    # green_grass
    4: (50, 125, 46),     # tree
    5: (201, 130, 39),    # water (bluish)
}


class Segmenter:
    def __init__(self, model_path, num_classes=6, input_size=512,
                 class_map=None, device=None):
        self.model_path = model_path
        self.num_classes = num_classes
        self.input_size = input_size
        self.class_map = class_map or DEFAULT_CLASS_MAP
        self._device = device
        self._torch = None
        self._model = None
        self._running = False

    @property
    def is_running(self):
        return self._running

    def start(self):
        if self._model is None:
            import torch
            from torchvision.models.segmentation import deeplabv3_mobilenet_v3_large
            from torchvision.models.segmentation.deeplabv3 import DeepLabHead

            self._torch = torch
            self._device = self._device or (
                "cuda" if torch.cuda.is_available() else "cpu")
            print(f"[SEG] loading {self.model_path} on {self._device}")

            # MobileNetV3-Large backbone (960-ch features) + 6-class DeepLab head.
            # No downloads: the trained checkpoint supplies all weights.
            model = deeplabv3_mobilenet_v3_large(
                weights=None, weights_backbone=None, aux_loss=False)
            model.classifier = DeepLabHead(960, self.num_classes)

            try:
                # pure state_dict -> weights_only=True is safe and silences
                # the FutureWarning on newer torch
                state = torch.load(self.model_path, map_location=self._device,
                                   weights_only=True)
            except TypeError:
                # older torch without the weights_only kwarg
                state = torch.load(self.model_path, map_location=self._device)
            # The checkpoint may carry an aux_classifier (used only in training);
            # we never use ["aux"] at inference, so drop those keys.
            state = {k: v for k, v in state.items()
                     if not k.startswith("aux_classifier")}
            missing, unexpected = model.load_state_dict(state, strict=False)
            if unexpected:
                print(f"[SEG] ignored {len(unexpected)} unexpected key(s)")
            if missing:
                print(f"[SEG] WARNING: {len(missing)} missing key(s), e.g. {missing[:3]}")

            model.eval().to(self._device)
            self._model = model
        self._running = True

    def stop(self):
        # Pause inference; the model stays resident so resuming is instant.
        self._running = False

    def _predict_mask(self, frame_bgr):
        torch = self._torch
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (self.input_size, self.input_size))
        x = resized.astype(np.float32) / 255.0
        x = torch.from_numpy(x).permute(2, 0, 1).unsqueeze(0).to(self._device)
        with torch.no_grad():
            out = self._model(x)["out"]
            mask = torch.argmax(out, dim=1).squeeze(0).cpu().numpy()
        return mask.astype(np.uint8)

    def _mask_to_terrain(self, mask):
        counts = {k: 0 for k in TERRAIN_KEYS}
        for cls_idx, key in self.class_map.items():
            if key in counts:
                counts[key] += int(np.count_nonzero(mask == cls_idx))
        total = sum(counts.values())
        if total <= 0:
            return {k: 0.0 for k in TERRAIN_KEYS}
        return {k: round(counts[k] / total, 3) for k in TERRAIN_KEYS}

    def _overlay(self, frame, mask):
        h, w = frame.shape[:2]
        mask_full = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        color = np.zeros((h, w, 3), dtype=np.uint8)
        for cls_idx, bgr in CLASS_COLORS.items():
            color[mask_full == cls_idx] = bgr
        area = mask_full > 0
        out = frame.copy()
        blended = cv2.addWeighted(frame, 0.55, color, 0.45, 0)
        out[area] = blended[area]
        return out

    def segment(self, frame):
        if not self._running or self._model is None:
            return {k: 0.0 for k in TERRAIN_KEYS}, frame
        mask = self._predict_mask(frame)
        terrain = self._mask_to_terrain(mask)
        annotated = self._overlay(frame, mask)
        return terrain, annotated
