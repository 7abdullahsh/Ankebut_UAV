"""ANKEBUT - FULL MISSION: model switching + fire-reaction flight logic.

Run this on the Jetson and it will: ARM -> switch to AUTO and start the mission
(the mission's own TAKEOFF waypoint lifts off) -> wait until airborne -> run
YOLO patrol -> on a confirmed fire, interrupt with GUIDED to climb + segment ->
resume AUTO. The Jetson never commands a raw takeoff altitude; the climb comes
from YOUR mission, so set a safe takeoff height in Mission Planner.

  >>> REQUIREMENT: the AUTO mission's FIRST item must be a TAKEOFF waypoint at a
      safe altitude (e.g. 15-20 m). For a copter, AUTO will not lift off
      otherwise and the script will time out waiting to go airborne. <<<

Sequence:
  1. START     : on ENTER -> GUIDED, ARM, then AUTO + MISSION_START. The
                 mission takes off on its own (no hardcoded takeoff altitude).
  2. WAIT       : wait until altitude > AIRBORNE_ALT_M, then YOLO ON -> PATROL.
  3. PATROL    : YOLO ON, fly the route, watch for fire.
  4. CONFIRMING: fire must hold FIRE_CONFIRM_S; drops out -> PATROL. GCS is not
                 told about the fire until confirmed. On confirm -> YOLO off.
  5. CLIMBING  : GUIDED, climb +ANALYSIS_CLIMB_M. NO model runs.
  6. ANALYZING : ONLY after the climb -> segmenter ON for ANALYSIS_DURATION_S
                 -> fetch wind -> send fire_event to the GCS.
  7. DESCENDING: descend to cruise alt -> AUTO (resume route) -> YOLO ON ->
                 DETECT_COOLDOWN_S ignore window -> PATROL.
  ... loop until a NEW fire, or the route finishes -> LAND.
  q / Ctrl-C any time -> if airborne LAND in place, else just disarm. A crash
  while airborne also triggers LAND.

GPU NOTE: the Orin Nano cannot hold YOLO and the segmenter on the GPU at once
(it OOMs). YOLO runs on the GPU, the segmenter on the CPU - never sharing the
GPU. CPU segmentation is slower, fine for the 10 s burst (tune SEG_CPU_SIZE).

  ##############################################################
  #  SITL: simulation only - safe.                             #
  #  REAL HW: the Jetson ARMS and starts AUTO - the drone WILL #
  #  take off. Open area, RC ready to take over. Confirm the   #
  #  mission + takeoff altitude in Mission Planner first.      #
  ##############################################################

    python test_full_mission.py            # GCS IP from config.py
    python test_full_mission.py <GCS_IP>   # override the GCS IP
"""

import sys
import time
import select
import threading

from pymavlink import mavutil

import config
from camera import Camera
from fire_detector import FireDetector
from segmenter import Segmenter, TERRAIN_KEYS
from video_sender import VideoSender
from gcs_link import GcsLink
from wind_service import WindService

# ---- tunables (from config, with safe fallbacks) --------------------------
CONFIRM_S = getattr(config, "FIRE_CONFIRM_S", 4.0)       # fire must hold this long
LOST_GRACE_S = 0.5                                       # tolerate brief misses
ANALYZE_S = getattr(config, "ANALYSIS_DURATION_S", 10)   # segmenter-on duration
CLIMB_M = getattr(config, "ANALYSIS_CLIMB_M", 5)         # extra altitude for analysis
AIRBORNE_ALT = getattr(config, "AIRBORNE_ALT_M", 2.0)    # above this = "flying"
AIRBORNE_TIMEOUT_S = getattr(config, "AIRBORNE_TIMEOUT_S", 30)  # abort if it never climbs
COOLDOWN_S = getattr(config, "DETECT_COOLDOWN_S", 15)    # ignore re-trigger after resume
GUIDED_MODE = getattr(config, "HOVER_MODE", "GUIDED")
AUTO_MODE = getattr(config, "CRUISE_MODE", "AUTO")
RTL_MODE = getattr(config, "RETURN_MODE", "RTL")
SEND_DT = 1.0 / max(1, getattr(config, "DETECTION_SEND_HZ", 10))
ALT_TOL = 0.5                                            # altitude "reached" window (m)
ARM_TIMEOUT_S = 25                                       # give up arming after this
# Segmenter runs on the CPU; smaller input than the GPU 512 so each CPU
# inference is fast enough not to stall the loop. Ratios are robust to this.
SEG_CPU_SIZE = getattr(config, "SEG_CPU_INPUT_SIZE", 256)


def key_line():
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.readline().strip().lower()
    return None


def has_fire(dets):
    return any(d.get("label") == "fire" for d in dets)


def assess_risk(terrain, wind):
    """Simple fuel + wind heuristic -> {level, primary_fuel, note}."""
    dry = terrain.get("dry_grass", 0.0)
    tree = terrain.get("tree", 0.0)
    grass = terrain.get("green_grass", 0.0)
    fuels = {"dry_grass": dry, "tree": tree, "green_grass": grass}
    primary = max(fuels, key=fuels.get) if any(fuels.values()) else "none"
    fuel_score = dry * 1.0 + tree * 0.6 + grass * 0.3
    wspeed = float((wind or {}).get("speed_ms", 0) or 0)
    score = fuel_score + min(wspeed / 10.0, 0.5)
    level = "HIGH" if score >= 0.6 else "MODERATE" if score >= 0.3 else "LOW"
    return {"level": level, "primary_fuel": primary,
            "note": f"fuel~{primary}, wind {wspeed:.1f} m/s"}


class FlightLink:
    """Owns ALL MAVLink reading on a background thread (so the camera/model
    loop never stalls), and provides locked send helpers for the main thread."""

    def __init__(self, master):
        self.m = master
        self.lock = threading.Lock()
        self.lat = self.lon = self.alt = self.heading = None
        self.mode = None
        self.armed = None
        self.seq = None
        self.last_reached = -1
        self.wp_count = None
        self._run = False
        self._t = None

    def start(self):
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        m = self.m
        while self._run:
            msg = m.recv_match(blocking=True, timeout=0.5)
            if msg is None:
                continue
            t = msg.get_type()
            if t == "HEARTBEAT":
                if (msg.get_srcSystem() == m.target_system
                        and msg.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID):
                    self.mode = mavutil.mode_string_v10(msg)
                    self.armed = bool(msg.base_mode &
                                      mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            elif t == "GLOBAL_POSITION_INT":
                self.lat = msg.lat / 1e7
                self.lon = msg.lon / 1e7
                self.alt = msg.relative_alt / 1000.0
                self.heading = msg.hdg / 100.0 if msg.hdg != 65535 else None
            elif t == "MISSION_CURRENT":
                self.seq = msg.seq
            elif t == "MISSION_ITEM_REACHED":
                self.last_reached = msg.seq
            elif t == "MISSION_COUNT":
                self.wp_count = msg.count

    def stop(self):
        self._run = False
        if self._t is not None:
            self._t.join(timeout=2.0)

    # -- locked sends --------------------------------------------------------
    def request_streams(self, hz=4):
        with self.lock:
            self.m.mav.request_data_stream_send(
                self.m.target_system, self.m.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, hz, 1)

    def request_mission_count(self):
        with self.lock:
            self.m.mav.mission_request_list_send(
                self.m.target_system, self.m.target_component)

    def arm(self, a=True):
        with self.lock:
            self.m.mav.command_long_send(
                self.m.target_system, self.m.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                1 if a else 0, 0, 0, 0, 0, 0, 0)

    def set_mode(self, name):
        mapping = self.m.mode_mapping() or {}
        if name.upper() not in mapping:
            print(f"   '{name}' not available. Known: {list(mapping)}")
            return False
        with self.lock:
            self.m.mav.set_mode_send(
                self.m.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                mapping[name.upper()])
        return True

    def mission_start(self):
        with self.lock:
            self.m.mav.command_long_send(
                self.m.target_system, self.m.target_component,
                mavutil.mavlink.MAV_CMD_MISSION_START, 0,
                0, 0, 0, 0, 0, 0, 0)

    def goto(self, lat, lon, alt):
        with self.lock:
            self.m.mav.set_position_target_global_int_send(
                0, self.m.target_system, self.m.target_component,
                mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                0b0000111111111000,
                int(lat * 1e7), int(lon * 1e7), float(alt),
                0, 0, 0, 0, 0, 0, 0, 0)

    def drone_dict(self):
        return {"lat": self.lat, "lon": self.lon,
                "alt_m": self.alt, "heading_deg": self.heading}


def main():
    gcs_host = sys.argv[1] if len(sys.argv) > 1 else config.GCS_HOST
    conn = config.MAVLINK_CONNECTION
    baud = config.MAVLINK_BAUD

    # ---- connect flight controller ----------------------------------------
    print(f"[MISSION] connecting flight link: {conn}")
    if conn.startswith(("udp:", "tcp:", "udpin:")):
        master = mavutil.mavlink_connection(conn)
    else:
        master = mavutil.mavlink_connection(conn, baud=baud)
    print("[MISSION] waiting for heartbeat ...")
    master.wait_heartbeat()
    print(f"[MISSION] heartbeat OK -> system {master.target_system}")

    link = FlightLink(master)
    link.start()
    link.request_streams(4)
    link.request_mission_count()
    time.sleep(1.0)
    if link.armed:
        print("[MISSION] already armed - disarm first, then rerun.")
        link.stop()
        master.close()
        return

    # ---- camera + models + GCS links --------------------------------------
    cam = Camera(source=config.CAMERA_INDEX, width=config.FRAME_WIDTH,
                 height=config.FRAME_HEIGHT, fps=config.FRAME_FPS)
    det = FireDetector(config.YOLO_MODEL_PATH,
                       conf_threshold=config.FIRE_CONF_THRESHOLD,
                       fire_labels=config.FIRE_LABELS,
                       smoke_labels=config.SMOKE_LABELS)
    seg = Segmenter(config.SEG_MODEL_PATH, num_classes=config.SEG_NUM_CLASSES,
                    input_size=SEG_CPU_SIZE, class_map=config.SEG_CLASS_MAP,
                    device="cpu")
    vid = VideoSender(gcs_host, config.GCS_VIDEO_PORT,
                      width=config.FRAME_WIDTH, height=config.FRAME_HEIGHT,
                      fps=config.FRAME_FPS)
    gcs = GcsLink(gcs_host, config.GCS_DATA_PORT)
    # Wind for the UI: open-meteo needs no API key. Fall back to it if the
    # OpenWeatherMap key is missing/placeholder.
    _wx_provider = getattr(config, "WEATHER_PROVIDER", "open-meteo")
    _wx_key = getattr(config, "WEATHER_API_KEY", "") or ""
    if _wx_provider == "openweathermap" and (
            not _wx_key or "YOUR_API_KEY" in _wx_key.upper()):
        print("[MISSION] no valid OpenWeatherMap key -> using open-meteo (key-free)")
        _wx_provider = "open-meteo"
    wind_svc = WindService(_wx_provider, _wx_key,
                           getattr(config, "WEATHER_TIMEOUT_S", 4))
    print(f"[MISSION] wind provider: {_wx_provider}")

    print(f"[MISSION] feed -> GCS {gcs_host}: video {config.GCS_VIDEO_PORT}, "
          f"data {config.GCS_DATA_PORT}")
    print(f"[MISSION] AIRBORNE>{AIRBORNE_ALT}m  CONFIRM={CONFIRM_S}s  "
          f"CLIMB=+{CLIMB_M}m  ANALYZE={ANALYZE_S}s  COOLDOWN={COOLDOWN_S}s")

    cam.start()
    vid.start()
    # YOLO on GPU, segmenter on CPU - safe to hold both resident.
    print("[MISSION] loading YOLO (GPU) + segmenter (CPU, one-time) ...")
    det.start()
    seg.start()
    seg.stop()          # paused but resident in RAM; YOLO is the active one

    # ---- shared mission state ---------------------------------------------
    wind_holder = {"wind": None}

    def fetch_wind_async(lat, lon):
        wind_holder["wind"] = None

        def _w():
            wind_holder["wind"] = wind_svc.get_wind(lat, lon)
        threading.Thread(target=_w, daemon=True).start()

    seg_sum = {k: 0.0 for k in TERRAIN_KEYS}
    seg_n = 0

    fire_first = fire_last = 0.0
    fire_loc = None
    confirmed_fire_conf = None        # set on confirm -> tells the GCS "fire!"
    cruise_alt = AIRBORNE_ALT
    climb_target = None
    analyze_until = 0.0
    cooldown_until = 0.0
    descend_t0 = 0.0
    arm_t0 = 0.0
    last_arm_t = 0.0
    wait_t0 = 0.0

    last_send = 0.0
    last_print = 0.0
    running = True

    def finish(reason):
        """If airborne, LAND in place; if on the ground, just disarm. Keeps the
        feed alive and waits for disarm."""
        print(f"\n[MISSION] FINISH: {reason}")
        airborne = link.alt is not None and link.alt > AIRBORNE_ALT
        if airborne:
            print(f"[MISSION] airborne -> LAND (landing in place)")
            link.set_mode("LAND")
            t0 = time.time()
            while time.time() - t0 < 120:
                f = cam.read()
                if f is not None:
                    vid.send(f)
                    gcs.send_detection("LAND", [], link.drone_dict())
                if link.armed is False:
                    print("[MISSION] landed + disarmed.")
                    return
                time.sleep(0.05)
            print("[MISSION] LAND timed out - sending disarm.")
        else:
            print("[MISSION] on the ground -> disarming.")
        link.arm(False)

    # ---- ENTER to begin ---------------------------------------------------
    print("\n>>> The Jetson will ARM and start AUTO - the drone WILL TAKE OFF "
          "via the mission. Open area, RC ready. <<<")
    try:
        input(">>> Press ENTER to begin (Ctrl-C to abort): ")
    except KeyboardInterrupt:
        print("\n[MISSION] aborted.")
        link.stop(); cam.stop(); vid.close(); gcs.close(); master.close()
        return

    # Arm in GUIDED (a permissive arming mode), then hand to AUTO + mission.
    link.set_mode(GUIDED_MODE)
    link.arm(True)
    arm_t0 = time.time()
    last_arm_t = arm_t0
    phase = "START"
    print("[MISSION] START: GUIDED + arming ...")

    try:
        while running:
            if key_line() == "q":
                finish("user quit")
                break

            frame = cam.read()
            if frame is None:
                time.sleep(0.01)
                continue
            now = time.time()

            last_dets = []                          # live YOLO dets this tick
            analyze_remaining = None
            annotated = frame                       # default: raw feed

            # ============================ STATE MACHINE ====================
            if phase == "START":
                # Wait until armed, then switch to AUTO and start the mission.
                if link.armed:
                    link.set_mode(AUTO_MODE)
                    link.mission_start()            # mission TAKEOFF lifts off
                    wait_t0 = now
                    phase = "WAIT_AIRBORNE"
                    print("   ARMED -> AUTO + mission start "
                          "(taking off via the mission) ...")
                else:
                    if now - arm_t0 > ARM_TIMEOUT_S:
                        print("[MISSION] ARM FAILED/timed out (GPS/EKF, safety "
                              "switch, pre-arm in MP). Aborting.")
                        break
                    if now - last_arm_t > 3:
                        link.arm(True)              # gentle retry every 3 s
                        last_arm_t = now

            elif phase == "WAIT_AIRBORNE":
                if link.alt is not None and link.alt > AIRBORNE_ALT:
                    det.start()
                    cruise_alt = link.alt
                    phase = "PATROL"
                    print(f"   airborne at {link.alt:.1f} m -> YOLO ON "
                          f"(patrolling)\n")
                elif now - wait_t0 > AIRBORNE_TIMEOUT_S:
                    print("[MISSION] never climbed after AUTO+mission start. "
                          "Does the mission START WITH A TAKEOFF waypoint? "
                          "Aborting (disarming).")
                    finish("no takeoff")
                    break

            elif phase == "PATROL":
                last_dets, annotated = det.detect(frame)
                if (link.wp_count and link.wp_count > 0
                        and link.last_reached >= link.wp_count - 1):
                    finish("route complete")
                    break
                if has_fire(last_dets) and now >= cooldown_until:
                    fire_first = fire_last = now
                    phase = "CONFIRMING"
                    print(f"   ? possible fire -> confirming it holds {CONFIRM_S}s "
                          f"(GCS not alerted yet)")

            elif phase == "CONFIRMING":
                last_dets, annotated = det.detect(frame)     # YOLO stays ON
                if has_fire(last_dets):
                    fire_last = now
                    held = now - fire_first
                    if held >= CONFIRM_S:
                        det.stop()                  # pause YOLO (stays on GPU)
                        confirmed_fire_conf = max(
                            (d["confidence"] for d in last_dets
                             if d["label"] == "fire"), default=0.9)
                        fire_loc = {"lat": link.lat, "lon": link.lon,
                                    "alt_m": link.alt}
                        cruise_alt = link.alt
                        climb_target = (link.alt or 0.0) + CLIMB_M
                        seg_sum = {k: 0.0 for k in TERRAIN_KEYS}
                        seg_n = 0
                        if link.lat is not None:
                            fetch_wind_async(link.lat, link.lon)
                            link.set_mode(GUIDED_MODE)
                            link.goto(link.lat, link.lon, climb_target)
                        phase = "CLIMBING"
                        print(f"   >>> FIRE CONFIRMED (GCS alerted) -> YOLO off, "
                              f"GUIDED, climb {cruise_alt:.1f} -> {climb_target:.1f} m")
                else:
                    if now - fire_last > LOST_GRACE_S:
                        phase = "PATROL"
                        print("   x fire lost before confirm -> PATROL (false alarm)")

            elif phase == "CLIMBING":
                # NO model runs here - segmenter starts ONLY after the climb.
                if (link.alt is not None and climb_target is not None
                        and link.alt >= climb_target - ALT_TOL):
                    seg.start()                     # CPU segmenter ON now
                    analyze_until = now + ANALYZE_S
                    phase = "ANALYZING"
                    print(f"   reached {link.alt:.1f} m -> segmenter ON (CPU) for "
                          f"{ANALYZE_S}s")

            elif phase == "ANALYZING":
                terrain, annotated = seg.segment(frame)
                for k in TERRAIN_KEYS:
                    seg_sum[k] += terrain.get(k, 0.0)
                seg_n += 1
                analyze_remaining = max(0.0, analyze_until - now)
                if now >= analyze_until:
                    seg.stop()
                    avg = {k: (round(seg_sum[k] / seg_n, 3) if seg_n else 0.0)
                           for k in TERRAIN_KEYS}
                    wind = wind_holder["wind"]
                    risk = assess_risk(avg, wind)
                    gcs.send_fire_event(fire_loc, avg, wind, risk)
                    top = sorted(avg.items(), key=lambda kv: kv[1], reverse=True)
                    print(f"   >>> analysis done. terrain="
                          f"{', '.join(f'{k}:{v:.0%}' for k, v in top if v > 0)}"
                          f" | wind={wind} | risk={risk['level']}")
                    if link.lat is not None and cruise_alt is not None:
                        link.goto(link.lat, link.lon, cruise_alt)
                    descend_t0 = now
                    phase = "DESCENDING"
                    print(f"   descending back to {cruise_alt:.1f} m ...")

            elif phase == "DESCENDING":
                reached = (link.alt is not None
                           and link.alt <= cruise_alt + ALT_TOL)
                if reached or now - descend_t0 > 15:
                    link.set_mode(AUTO_MODE)        # resume the patrol route
                    link.mission_start()
                    det.start()                     # YOLO ON again
                    confirmed_fire_conf = None       # clear GCS fire alert
                    cooldown_until = now + COOLDOWN_S
                    phase = "PATROL"
                    print(f"   >>> back on route: AUTO, YOLO ON, ignoring "
                          f"re-triggers for {COOLDOWN_S}s\n")

            # ============================ STREAM + REPORT ==================
            vid.send(annotated)

            # HOLD fire from the GCS until it is confirmed. After confirm we
            # report the confirmed fire through climb/analyze/descend.
            if phase in ("CLIMBING", "ANALYZING", "DESCENDING") and \
                    confirmed_fire_conf is not None:
                gstate = phase
                dets_send = [GcsLink.detection("fire", confirmed_fire_conf)]
            elif phase in ("PATROL", "CONFIRMING"):
                gstate = "PATROL" if phase == "PATROL" else "LOCKED"
                dets_send = [GcsLink.detection(d["label"], d["confidence"])
                             for d in last_dets if d["label"] != "fire"]
            else:  # START / WAIT_AIRBORNE
                gstate = "TAKEOFF"
                dets_send = []

            if now - last_send >= SEND_DT:
                gcs.send_detection(gstate, dets_send, link.drone_dict(),
                                   analyze_remaining)
                last_send = now

            if now - last_print >= 1.0:
                pos = (f"{link.alt:.1f}m" if link.alt is not None else "?")
                extra = ""
                if phase == "CONFIRMING":
                    extra = f" held {now - fire_first:.1f}/{CONFIRM_S:.0f}s"
                elif phase == "ANALYZING":
                    extra = f" {analyze_remaining:.0f}s left"
                elif phase == "PATROL" and now < cooldown_until:
                    extra = f" (cooldown {cooldown_until - now:.0f}s)"
                livfire = "FIRE" if has_fire(last_dets) else ""
                print(f"[{phase:<12}] mode={link.mode or '?':<8} alt={pos:<7}"
                      f" {livfire}{extra}")
                last_print = now

    except KeyboardInterrupt:
        print("\n[MISSION] Ctrl-C")
        try:
            finish("ctrl-c")
        except Exception as e:
            print("[MISSION] finish error:", e)
    except Exception as e:
        # Any crash mid-flight: do NOT leave it hovering - bring it down.
        print(f"\n[MISSION] ERROR: {e}")
        try:
            finish("error - landing for safety")
        except Exception as e2:
            print("[MISSION] finish error:", e2)
        raise
    finally:
        det.stop()
        seg.stop()
        vid.close()
        cam.stop()
        gcs.close()
        link.stop()
        master.close()
        print("[MISSION] all stopped. Done.")


if __name__ == "__main__":
    main()
