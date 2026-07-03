"""ANKEBUT - STEP 4 (automatic, with fire confirmation).

Cycle (models only, no flight):

  PATROL    : YOLO ON. Watching. First time YOLO sees FIRE -> go to CONFIRMING.
  CONFIRMING: YOLO still ON. The fire must PERSIST for CONFIRM_S seconds to be
              real (not a momentary red object). If fire drops out (for more
              than a short grace) before that -> back to PATROL (false alarm).
              If it persists CONFIRM_S -> go to ANALYZE.
  ANALYZE   : YOLO OFF, segmenter ON, for ANALYZE_S seconds (terrain overlay).
              Then -> COOLDOWN.
  COOLDOWN  : both models OFF (raw feed), for COOLDOWN_S seconds, so the same
              fire doesn't instantly re-trigger. Then -> PATROL. Repeat forever.

The active model's annotated feed is streamed to the GCS so you can watch it.
During CONFIRMING you'll see the YOLO fire box held while it counts up.

Press 'q' + ENTER (or Ctrl-C) to quit.

USAGE (Jetson, ~/main):
    python test_model_cycle.py            # GCS IP from config.py
    python test_model_cycle.py <GCS_IP>   # override IP
"""

import sys
import time
import select

import config
from camera import Camera
from fire_detector import FireDetector
from segmenter import Segmenter
from video_sender import VideoSender

CONFIRM_S = getattr(config, "FIRE_CONFIRM_S", 4.0)      # must see fire this long
LOST_GRACE_S = 0.5                                      # tolerate brief frame misses
ANALYZE_S = getattr(config, "ANALYSIS_DURATION_S", 10)  # segmenter-on duration
COOLDOWN_S = getattr(config, "COOLDOWN_S", 3.0)         # blind wait after analyze


def key_line():
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.readline().strip().lower()
    return None


def has_fire(dets):
    return any(d.get("label") == "fire" for d in dets)


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else config.GCS_HOST

    cam = Camera(source=config.CAMERA_INDEX, width=config.FRAME_WIDTH,
                 height=config.FRAME_HEIGHT, fps=config.FRAME_FPS)
    det = FireDetector(config.YOLO_MODEL_PATH,
                       conf_threshold=config.FIRE_CONF_THRESHOLD,
                       fire_labels=config.FIRE_LABELS,
                       smoke_labels=config.SMOKE_LABELS)
    seg = Segmenter(config.SEG_MODEL_PATH, num_classes=config.SEG_NUM_CLASSES,
                    input_size=config.SEG_INPUT_SIZE, class_map=config.SEG_CLASS_MAP)
    vid = VideoSender(host, config.GCS_VIDEO_PORT,
                      width=config.FRAME_WIDTH, height=config.FRAME_HEIGHT,
                      fps=config.FRAME_FPS)

    print(f"[CYCLE] feed -> GCS {host}:{config.GCS_VIDEO_PORT}")
    print(f"[CYCLE] CONFIRM_S={CONFIRM_S}s  ANALYZE_S={ANALYZE_S}s  "
          f"COOLDOWN_S={COOLDOWN_S}s")
    cam.start()
    vid.start()

    print("[CYCLE] loading YOLO (segmenter loads on demand; Orin can't hold both) ...")
    det.start()
    phase = "PATROL"
    fire_first_seen = 0.0
    fire_last_seen = 0.0
    analyze_until = 0.0
    cooldown_until = 0.0
    print("[CYCLE] running in PATROL (YOLO on). Press q+ENTER / Ctrl-C to quit.\n")

    last_print = 0.0
    info = ""
    try:
        while True:
            if key_line() == "q":
                break

            frame = cam.read()
            if frame is None:
                time.sleep(0.01)
                continue
            now = time.time()

            if phase == "PATROL":
                dets, annotated = det.detect(frame)
                info = (", ".join(f"{d['label']}:{d['confidence']:.2f}"
                                  for d in dets) if dets else "watching...")
                if has_fire(dets):
                    fire_first_seen = now
                    fire_last_seen = now
                    phase = "CONFIRMING"
                    print(f"   ? possible fire -> confirming it holds for {CONFIRM_S}s")

            elif phase == "CONFIRMING":
                dets, annotated = det.detect(frame)     # YOLO stays on
                if has_fire(dets):
                    fire_last_seen = now
                    held = now - fire_first_seen
                    info = f"confirming fire  {held:.1f}/{CONFIRM_S:.0f}s"
                    if held >= CONFIRM_S:
                        det.stop()
                        seg.start()
                        phase = "ANALYZE"
                        analyze_until = now + ANALYZE_S
                        print(f"   >>> FIRE CONFIRMED -> segmenter ON for {ANALYZE_S}s")
                else:
                    lost_for = now - fire_last_seen
                    info = f"fire flickered ({lost_for:.1f}s lost)"
                    if lost_for > LOST_GRACE_S:
                        phase = "PATROL"
                        print("   x fire lost before confirm -> back to PATROL "
                              "(false alarm)")

            elif phase == "ANALYZE":
                terrain, annotated = seg.segment(frame)
                top = sorted(terrain.items(), key=lambda kv: kv[1], reverse=True)
                info = (", ".join(f"{k}:{v:.0%}" for k, v in top if v > 0) or "—")
                if now >= analyze_until:
                    seg.stop()
                    phase = "COOLDOWN"
                    cooldown_until = now + COOLDOWN_S
                    print(f"   >>> analysis done -> {COOLDOWN_S}s cooldown")

            else:  # COOLDOWN
                annotated = frame                        # raw feed, no model
                info = f"waiting {max(0.0, cooldown_until - now):.1f}s"
                if now >= cooldown_until:
                    det.start()
                    phase = "PATROL"
                    print("   >>> cooldown done -> YOLO ON (watching for fire)\n")

            vid.send(annotated)

            if now - last_print >= 1.0:
                print(f"[{phase:<10}] {info}")
                last_print = now

    except KeyboardInterrupt:
        print("\n[CYCLE] Ctrl-C")
    finally:
        det.stop()
        seg.stop()
        vid.close()
        cam.stop()
        print("[CYCLE] models stopped, camera + feed closed. Done.")


if __name__ == "__main__":
    main()
