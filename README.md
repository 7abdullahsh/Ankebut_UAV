# ANKEBUT — Autonomous Wildfire-Detection UAV

ANKEBUT is an autonomous aerial monitoring system that scans forested areas, detects fire and smoke, and generates **wind-aware, geo-referenced fire-spread risk alerts** for a ground operator in real time.

The system combines an onboard companion computer running deep-learning perception with a Windows ground control station (GCS) that embeds Mission Planner, live video, telemetry, and a fire-risk dashboard.

---

## System overview

```
   RGB camera ─► Jetson Orin Nano ─► segmentation + YOLOv11 detection ─► risk fusion ─► alert
                        │                                                        │
                        ├── H.264 video ──────────► WiFi link ──────────►  GCS video panel
                        └── MAVLink ◄──► Cube Orange ◄──SiK radio──►  GCS telemetry + Mission Planner
                                                                            │
                                                       Wind data (fetched on the GCS for the fire's GPS)
```

- **Airframe:** S550 hexacopter
- **Flight controller:** Cube Orange (ArduPilot / ArduCopter, HEXAROTOR)
- **Companion computer:** NVIDIA Jetson Orin Nano (Python 3.10)
- **Perception:**
  - YOLOv11 fire/smoke detection — a production model plus a **pillow proxy model** (red = fire, green = smoke) for safe validation without real fire
  - DeepLabV3 + MobileNetV3-Large land-cover segmentation (classes: tree, green grass, dry grass, soil, water)
- **Risk model:** per-class combustibility weighted by the wind vector (downwind regions rated more critical) → Moderate / High / Critical + probable spread direction
- **Ground station:** Windows PySide6 app with an embedded Mission Planner, live video, telemetry, and a fire-event panel
- **Links:** dedicated 5 GHz WiFi (video + alerts over UDP) and a SiK telemetry radio (MAVLink)

---

## Autonomous mission logic

1. Arm in GUIDED, then switch to **AUTO** and start the pre-planned grid mission.
2. The AUTO mission's own **TAKEOFF waypoint** lifts off to a safe altitude (no companion-commanded takeoff).
3. Grid patrol with YOLOv11 running on the live frames.
4. On a fire/smoke detection **confirmed over successive frames**, switch to **GUIDED**, climb and hold above the target.
5. Run land-cover segmentation + wind-aware risk fusion, then send a geo-referenced fire alert to the GCS.
6. Descend, return to **AUTO**, and resume the grid — with a short **cooldown** so the same target doesn't re-trigger.

> Wind data is retrieved **on the GCS side** using the fire's transmitted coordinates, because the ground station has a reliable internet connection in the field while the drone's data link does not.

---

## Repository structure

```
Ankebut_UAV/
├── README.md
├── .gitignore
├── gcs/                     # Windows PySide6 ground control station
│   ├── main_window.py
│   ├── detection_panel.py
│   ├── video_widget.py
│   ├── mission_planner_host.py
│   ├── telemetry_panel.py
│   ├── log_receiver.py
│   ├── control_bar.py
│   └── config.py
├── jetson/                  # Jetson Orin Nano airborne mission software
│   ├── main_full_mission.py
│   ├── fire_detector.py
│   ├── segmenter.py
│   ├── camera.py
│   ├── video_sender.py
│   ├── gcs_link.py
│   ├── wind_service.py
│   └── config.py
├── models/                  # model weights (see "Model weights" below — not committed directly)
├── tests/                   # standalone hardware / integration test scripts
└── docs/                    # thesis, architecture diagrams, screenshots
```

---

## Running the system

### Ground control station (Windows)

```bash
cd gcs
python -m venv .venv && .venv\Scripts\activate
pip install PySide6 opencv-python pymavlink numpy
python main_window.py
```

Mission Planner must be installed locally (the GCS embeds it). Network ports are defined in `gcs/config.py`.

### Onboard software (Jetson Orin Nano)

```bash
cd jetson
pip install ultralytics opencv-python pymavlink numpy torch torchvision
python main_full_mission.py
```

Requires the flight controller on the serial link (e.g. `/dev/ttyTHS1`), the 5 GHz WiFi access point running, and the model weights in `models/`. Ports and paths are set in `jetson/config.py`.

---

## Model weights

Model weights (`*.pt`, `*.pth`) are **not committed to git** (they are large and change often). Options:

- Track them with **Git LFS**: `git lfs install && git lfs track "*.pt" "*.pth"`, or
- Host them in a GitHub Release / cloud drive and download into `models/` before running.

---

## Notes

- The proxy pillow model allows the full detection → alert pipeline to be validated safely, without generating real fire or smoke.
- Land-cover segmentation is untrained on man-made surfaces (roads, pavement) and may mislabel them; this is a known limitation.
- This is a research / thesis UAV system. Real flight testing must follow local regulations and safety procedures.
