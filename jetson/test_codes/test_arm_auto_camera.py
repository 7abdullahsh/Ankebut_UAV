"""ANKEBUT - ARM + AUTO from the Jetson, with RAW camera -> GCS (BENCH, props-off).

Same as test_arm_auto.py, plus: the moment you launch it, the RAW camera feed
(no models, no boxes) streams to the GCS video panel and keeps streaming the
whole time - including while the script waits for your keypresses, because the
camera runs on its own background thread.

Flow:
  - on launch: camera opens, raw feed starts streaming to the GCS video panel
  - press ENTER    -> ARM   (confirm propellers are OFF)
  - type 'o' ENTER -> switch to AUTO
  - live-monitor mode / armed / waypoint / position (camera still streaming)
  - on exit -> DISARM (safety), then stop the camera

  ##############################################################
  #  BENCH USE ONLY - PROPELLERS REMOVED. Armed (and in AUTO)  #
  #  the MOTORS WILL SPIN. Props off = safe; keep hands clear. #
  ##############################################################

USAGE (Jetson, ~/main):
    python test_arm_auto_camera.py                 # uses GCS_HOST from config.py
    python test_arm_auto_camera.py <GCS_IP>        # override the IP if needed
"""

import sys
import time
import threading
from pymavlink import mavutil

import config
from camera import Camera
from video_sender import VideoSender

AUTO_MODE = getattr(config, "CRUISE_MODE", "AUTO")


# ---------------- camera streaming (background thread) ----------------
def stream_loop(cam, vid, stop_event):
    period = 1.0 / max(config.FRAME_FPS, 1)
    next_t = time.time()
    while not stop_event.is_set():
        frame = cam.read()
        if frame is not None:
            vid.send(frame)                 # RAW frame, no models
        next_t += period
        sleep = next_t - time.time()
        if sleep > 0:
            time.sleep(sleep)
        else:
            next_t = time.time()


# ---------------- MAVLink helpers ----------------
def heartbeat(master, timeout=5):
    msg = master.recv_match(type="HEARTBEAT", blocking=True, timeout=timeout)
    if msg is None:
        return None, None
    return (mavutil.mode_string_v10(msg),
            bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED))


def send_arm(master, arm=True):
    master.mav.command_long_send(
        master.target_system, master.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
        1 if arm else 0, 0, 0, 0, 0, 0, 0)


def wait_armed(master, target, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        mode, armed = heartbeat(master)
        if armed is not None:
            print(f"   mode={mode}  armed={armed}")
            if armed == target:
                return True
        time.sleep(0.5)
    return False


def set_mode(master, name):
    mapping = master.mode_mapping() or {}
    if name not in mapping:
        print(f"   '{name}' not available. Known: {list(mapping)}")
        return False
    master.mav.set_mode_send(
        master.target_system,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mapping[name])
    return True


def wait_mode(master, target, timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        mode, armed = heartbeat(master)
        if mode is not None:
            print(f"   mode={mode}  armed={armed}")
            if mode == target:
                return True
        time.sleep(0.5)
    return False


def monitor(master):
    print("\n[CAM_AUTO] monitoring (camera still streaming). Ctrl-C to stop + disarm.\n")
    mode = armed = lat = lon = alt = seq = None
    last = 0.0
    while True:
        msg = master.recv_match(blocking=True, timeout=1.0)
        if msg is not None:
            t = msg.get_type()
            if t == "HEARTBEAT":
                mode = mavutil.mode_string_v10(msg)
                armed = bool(msg.base_mode &
                             mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            elif t == "GLOBAL_POSITION_INT":
                lat = msg.lat / 1e7
                lon = msg.lon / 1e7
                alt = msg.relative_alt / 1000.0
            elif t == "MISSION_CURRENT":
                seq = msg.seq
        now = time.time()
        if now - last >= 1.0:
            armed_txt = (("ARMED" if armed else "disarmed")
                         if armed is not None else "?")
            pos_txt = (f"alt {alt:+.1f}m  {lat:.6f},{lon:.6f}"
                       if lat is not None else "no position")
            print(f"mode={(mode or '?'):<9} {armed_txt:<9} "
                  f"wp={seq if seq is not None else '-'}  | {pos_txt}")
            last = now


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else config.GCS_HOST
    conn = sys.argv[2] if len(sys.argv) > 2 else config.MAVLINK_CONNECTION
    baud = int(sys.argv[3]) if len(sys.argv) > 3 else config.MAVLINK_BAUD

    # ---- start the raw camera feed to the GCS first ----
    print(f"[CAM_AUTO] starting camera -> GCS {host}:{config.GCS_VIDEO_PORT} (raw, no models)")
    cam = Camera(source=config.CAMERA_INDEX, width=config.FRAME_WIDTH,
                 height=config.FRAME_HEIGHT, fps=config.FRAME_FPS)
    vid = VideoSender(host, config.GCS_VIDEO_PORT,
                      width=config.FRAME_WIDTH, height=config.FRAME_HEIGHT,
                      fps=config.FRAME_FPS)
    cam.start()
    vid.start()
    stop_event = threading.Event()
    streamer = threading.Thread(target=stream_loop, args=(cam, vid, stop_event),
                                daemon=True)
    streamer.start()
    print("[CAM_AUTO] camera streaming. Check the GCS video panel now.")

    master = None
    try:
        print(f"[CAM_AUTO] connecting to autopilot: {conn} (baud {baud} if serial)")
        if conn.startswith(("udp:", "tcp:", "udpin:")):
            master = mavutil.mavlink_connection(conn)
        else:
            master = mavutil.mavlink_connection(conn, baud=baud)
        print("[CAM_AUTO] waiting for heartbeat ...")
        master.wait_heartbeat()
        print(f"[CAM_AUTO] heartbeat OK -> system {master.target_system}")

        mode, armed = heartbeat(master)
        print(f"[CAM_AUTO] current: mode={mode}  armed={armed}")
        if armed:
            print("[CAM_AUTO] already armed - disarm from RC/MP first, then rerun.")
            return

        print("\n>>> Confirm ALL PROPELLERS ARE REMOVED. <<<")
        input(">>> Press ENTER to ARM (Ctrl-C to abort): ")
        print("[CAM_AUTO] sending ARM ...")
        send_arm(master, True)
        if not wait_armed(master, True, timeout=10):
            print("[CAM_AUTO] ARM FAILED/timed out. Check: safety switch, battery, "
                  "GPS/EKF, RC, pre-arm messages in Mission Planner.")
            return
        print("[CAM_AUTO] ARMED. (motors may idle-spin - props off, OK)")

        ans = input(f">>> Type 'o' + ENTER to switch to {AUTO_MODE} "
                    f"(anything else = stay, just monitor): ").strip().lower()
        if ans == "o":
            print(f"[CAM_AUTO] switching to {AUTO_MODE} "
                  f"(motors will spin up - props off, OK) ...")
            if set_mode(master, AUTO_MODE) and wait_mode(master, AUTO_MODE, 8):
                print(f"[CAM_AUTO] now in {AUTO_MODE}.")
            else:
                print(f"[CAM_AUTO] did NOT reach {AUTO_MODE} "
                      f"(needs GPS + uploaded mission). Monitoring anyway.")
        else:
            print("[CAM_AUTO] staying in current mode. Monitoring.")

        monitor(master)

    except KeyboardInterrupt:
        print("\n[CAM_AUTO] Ctrl-C")
    finally:
        # safety first: disarm
        if master is not None:
            print("[CAM_AUTO] DISARMING (safety) ...")
            try:
                send_arm(master, False)
                wait_armed(master, False, timeout=10)
            except Exception as e:
                print("[CAM_AUTO] disarm send error:", e)
            master.close()
        # then stop the camera feed
        stop_event.set()
        streamer.join(timeout=2)
        vid.close()
        cam.stop()
        print("[CAM_AUTO] camera stopped, link closed. Done.")


if __name__ == "__main__":
    main()
