"""ANKEBUT Jetson - fire / smoke detector (YOLO).

Wraps the Ultralytics YOLO model. Designed so the mission controller can
turn it ON during PATROL and OFF during analysis (so segmentation gets the
GPU, and there is no continuous re-triggering on the same fire).

  start()          - load the model (lazy import of ultralytics)
  stop()           - mark inactive (frees nothing heavy; model stays cached)
  detect(frame)    - returns (detections, annotated_frame)

`detections` is a list of:
    {"label": "fire", "confidence": 0.92, "bbox": [x, y, w, h]}
`annotated_frame` has the boxes drawn (this is what gets streamed to the GCS).

NOTE: assumes a standard Ultralytics interface. When Zehra's model is final,
only the class-name handling (FIRE_LABELS / SMOKE_LABELS) may need tweaking.
"""

import cv2


class FireDetector:
    def __init__(self, model_path, conf_threshold=0.8,
                 fire_labels=("fire",), smoke_labels=("smoke",)):
        self.model_path = model_path
        self.conf_threshold = conf_threshold
        self.fire_labels = tuple(s.lower() for s in fire_labels)
        self.smoke_labels = tuple(s.lower() for s in smoke_labels)
        self._model = None
        self._running = False

    @property
    def is_running(self):
        return self._running

    def start(self):
        if self._model is None:
            from ultralytics import YOLO  # lazy import (heavy)
            print(f"[YOLO] loading {self.model_path}")
            self._model = YOLO(self.model_path)
        self._running = True

    def stop(self):
        # Pause inference; the model stays resident on the GPU so resuming is
        # instant. (The segmenter runs on the CPU, so they never share the GPU.)
        self._running = False

    def detect(self, frame):
        """Run detection on one BGR frame -> (detections, annotated_frame)."""
        if not self._running or self._model is None:
            return [], frame

        results = self._model(frame, conf=self.conf_threshold, verbose=False)[0]
        annotated = frame.copy()

        detections = []
        names = results.names
        if results.boxes is not None:
            for box in results.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                label = str(names.get(cls_id, cls_id)).lower()

                if label in self.fire_labels:
                    norm = "fire"
                    color = (0, 0, 255)        # RED (BGR) for fire
                elif label in self.smoke_labels:
                    norm = "smoke"
                    color = (0, 200, 255)      # amber for smoke
                else:
                    continue                   # ignore other classes (balloon)

                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                text = f"{norm} {conf:.0%}"
                (tw, th), _ = cv2.getTextSize(
                    text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                cv2.rectangle(annotated, (x1, max(0, y1 - th - 6)),
                              (x1 + tw + 4, y1), color, -1)
                cv2.putText(annotated, text, (x1 + 2, max(th, y1 - 4)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
                            cv2.LINE_AA)

                detections.append({
                    "label": norm,
                    "confidence": round(conf, 3),
                    "bbox": [x1, y1, x2 - x1, y2 - y1],
                })
        return detections, annotated

    @staticmethod
    def has_fire(detections):
        return any(d.get("label") == "fire" for d in detections)
