"""ANKEBUT Jetson - annotated video sender (gst-launch subprocess).

Streams the already-annotated frame (boxes/masks drawn by the AI) to the GCS
as MPEG-TS H.264 over UDP - the exact format the ground-station video panel
(ui/video_widget.py) expects.

Method: pipe raw I420 frames into a `gst-launch-1.0` subprocess over stdin and
let GStreamer encode. This is the same pipeline as the working
ui_ssh_test_only_camera.py, so it does NOT need OpenCV to be built with
GStreamer (the system `gst-launch-1.0` does all the encoding).

Interface kept identical to the previous VideoSender (start / send / close)
so mission_controller.py and main.py are unchanged. The difference vs the
standalone test script: this one does not open the camera (camera.py owns it)
- it only *accepts* frames via send().

Encoder note: this uses software x264enc (works everywhere). To use the
Jetson hardware encoder instead, set encoder elements accordingly - see
ENCODER_HW below.
"""

import subprocess

import cv2

# Software encoder (matches your working test script). Works on any machine.
ENCODER_SW = [
    "x264enc", "tune=zerolatency", "speed-preset=ultrafast",
    "bitrate=1200", "key-int-max=15", "bframes=0", "threads=1",
]
# Jetson hardware encoder alternative (lower CPU). Swap in via use_hw=True.
ENCODER_HW = [
    "nvvidconv", "!", "nvv4l2h264enc", "insert-sps-pps=true", "idrinterval=15",
]


class VideoSender:
    def __init__(self, host, port, width=640, height=480, fps=30,
                 use_hw=False):
        self.host = host
        self.port = int(port)
        self.width = width
        self.height = height
        self.fps = fps
        self.use_hw = use_hw
        self._proc = None

    def _build_cmd(self):
        encoder = ENCODER_HW if self.use_hw else ENCODER_SW
        return [
            "gst-launch-1.0", "-q",
            "fdsrc", "!",
            "rawvideoparse",
            f"width={self.width}", f"height={self.height}",
            "format=i420", f"framerate={self.fps}/1", "!",
            "videoconvert", "!",
            *encoder, "!",
            "h264parse", "!",
            "mpegtsmux", "!",
            "udpsink", f"host={self.host}", f"port={self.port}",
        ]

    def start(self):
        self._proc = subprocess.Popen(self._build_cmd(),
                                      stdin=subprocess.PIPE)
        print(f"[VID] streaming to {self.host}:{self.port} "
              f"({'HW' if self.use_hw else 'SW'} encoder)")

    def send(self, frame):
        """Push one BGR frame to the GCS. Resizes + converts to I420."""
        if self._proc is None or self._proc.stdin is None:
            return
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height))
        frame_i420 = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)
        try:
            self._proc.stdin.write(frame_i420.tobytes())
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            print("[VID] GStreamer pipe closed")
            self._proc = None

    def close(self):
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
            except Exception:
                pass
            self._proc.wait()
            self._proc = None
            print("[VID] stream closed")
