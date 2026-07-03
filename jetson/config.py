"""ANKEBUT Jetson - central configuration.

All tunable values live here so nothing is hard-coded across the modules.
This file runs on the Orin Nano (NOT the GCS). Keep it separate from the
ground-station config.py.
"""

# ----------------------------------------------------------------------
# GCS link (where to send data + video)
# ----------------------------------------------------------------------
GCS_HOST = "192.168.4.9"        # IP of the PC running the ground station
GCS_DATA_PORT = 14560            # UDP port the GCS detection receiver listens on
                                 # (must match AI_RESULTS_UDP_PORT in the GCS config)
GCS_VIDEO_PORT = 5602            # UDP port for the annotated video stream
                                 # (must match VIDEO_UDP_PORT in the GCS config)

# How often to push live "detection" messages to the GCS while patrolling.
DETECTION_SEND_HZ = 10

# ----------------------------------------------------------------------
# Flight controller (MAVLink)
# ----------------------------------------------------------------------
# --- Mission Planner SITL over TCP ---
# Run the flight test ON THE SAME MACHINE as the SITL (the laptop) so localhost
# works. MP's SITL exposes TCP 5762/5763 for extra clients (5760 is taken by MP).
# MAVLINK_CONNECTION = "tcp:192.168.1.106:5762"
# --- alternatives ---
# Real Cube Orange over serial (use this on the Jetson for real flights):
MAVLINK_CONNECTION = "/dev/ttyTHS1"
# SITL on another PC, forwarded to this machine over UDP (see notes):
# MAVLINK_CONNECTION = "udpin:0.0.0.0:14550"

MAVLINK_BAUD = 115200            # only used for SERIAL connections (ignored for
                                 # tcp:/udp:). Matches the Cube Orange TELEM2 setup.

CRUISE_MODE = "AUTO"             # mode for flying the planned mission
HOVER_MODE = "GUIDED"           # mode used to hold position during analysis
RETURN_MODE = "RTL"             # mode at end of mission

# ----------------------------------------------------------------------
# Camera
# ----------------------------------------------------------------------
CAMERA_INDEX = 0                 # /dev/video0 (or a GStreamer pipeline string)
FRAME_WIDTH = 640                # matches the proven ui_ssh_test_only_camera.py
FRAME_HEIGHT = 480
FRAME_FPS = 30

# ----------------------------------------------------------------------
# Models (YOLO detection + segmentation)
# ----------------------------------------------------------------------
YOLO_MODEL_PATH = "models/pillowModel2.pt"
SEG_MODEL_PATH = "models/best_deeplabv3_v2.pth"

# A detection only counts as a real fire above this confidence.
FIRE_CONF_THRESHOLD = 0.70
# Labels the YOLO model emits, mapped to fire vs smoke.
# NOTE: this model was mislabeled - its "tree" class is actually smoke, so we
# treat the model's "tree" output as smoke on the GCS.
FIRE_LABELS = ("fire",)
SMOKE_LABELS = ("tree",)

# --- Segmentation: DeepLabV3-MobileNetV3-Large (.pth state_dict), 6 classes ---
# Class set + index order confirmed by the team (CVAT label order) and checked
# against seg_class_finder.py output. Index 0 is background, excluded from the
# terrain ratios. No fire class exists in the segmentation model.
SEG_NUM_CLASSES = 6
SEG_INPUT_SIZE = 512          # the model was trained at 512x512
# index -> GCS terrain key (background 0 excluded).
SEG_CLASS_MAP = {
    1: "tree",
    2: "green_grass",
    3: "dry_grass",
    4: "soil",
    5: "water",
}

# ----------------------------------------------------------------------
# Mission behaviour (the analysis sequence on each fire)
# ----------------------------------------------------------------------
ANALYSIS_DURATION_S = 10         # how long to hover + analyse a detected fire
ANALYSIS_CLIMB_M = 2             # extra altitude for a wider FOV (set 0 to stay put)
DETECT_COOLDOWN_S = 8           # ignore re-triggers for N s after resuming patrol
FIRE_CONFIRM_S = 0.5             # seconds YOLO must keep seeing fire before analyzing
COOLDOWN_S = 15                 # blind-flight wait after analysis (model cycle)
FIRE_HOVER_SEC = 10

# ----------------------------------------------------------------------
# Weather API (wind for the fire location)
# ----------------------------------------------------------------------
WEATHER_PROVIDER = "open-meteo"          # or "open-meteo" (no key needed)
WEATHER_API_KEY = ""
WEATHER_TIMEOUT_S = 4                    # HTTP timeout so it never blocks the loop
