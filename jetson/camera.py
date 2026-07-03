"""ANKEBUT Jetson - camera source.

The SINGLE place the camera is opened. A background thread grabs frames as
fast as they arrive and keeps only the latest one; read() hands out a copy.
This matters on the Orin Nano: the camera can only be opened once, and both
the AI models and the video sender need frames - so they all go through here.

`source` may be:
  * an int  -> /dev/videoN (USB / V4L2 webcam), e.g. 0
  * a str   -> a full GStreamer pipeline ending in `appsink`, e.g. a CSI
               camera via nvarguscamerasrc (recommended on Jetson)
"""

import threading
import cv2

class Camera:
    def __init__(self, source=0, width=1280, height=720, fps=30):
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps

        self._cap = None
        self._frame = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def _open(self):
        if isinstance(self.source, str):
            # full GStreamer pipeline string
            cap = cv2.VideoCapture(self.source, cv2.CAP_GSTREAMER)
        else:
            cap = cv2.VideoCapture(self.source)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
        return cap

    def start(self):
        self._cap = self._open()
        if not self._cap.isOpened():
            raise RuntimeError(f"[CAM] could not open camera source: {self.source}")
        self._running = True
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()
        print("[CAM] started")

    def _grab_loop(self):
        while self._running:
            ok, frame = self._cap.read()
            if not ok or frame is None:
                continue
            with self._lock:
                self._frame = frame

    def read(self):
        """Return a copy of the latest frame, or None if none yet."""
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()
        print("[CAM] stopped")
