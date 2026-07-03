"""ANKEBUT - STEP 2 test: TAKEOFF + AUTO <-> GUIDED flight logic (SITL or bench).

FLIGHT ONLY (no models, no camera). Proves the flight-mode half of the fire
reaction using the SAME MAVLink commands flight_controller.py uses
(set_mode_send + set_position_target_global_int_send, relative-alt frame),
PLUS a hands-off auto-takeoff so AUTO can actually fly without manual throttle.

Flow when you run the script:
  1. Arm + GUIDED + NAV_TAKEOFF climb to TAKEOFF_ALT  (hands-off auto-takeoff)
  2. Switch to AUTO  ->  drone continues its Mission Planner mission automatically

Keys (press key + ENTER, or just the key if tty is in cbreak mode):
  'f' + ENTER  -> "FIRE": record current alt, switch GUIDED, climb +CLIMB_M,
                  hover for FIRE_HOVER_SEC seconds, then automatically descend
                  back to cruise altitude and resume AUTO.
                  If 'f' is pressed again DURING the hover, the timer resets
                  (stays up longer).
  'q' + ENTER  -> LAND (if airborne) then disarm, and quit

  ##############################################################
  #  SITL: simulation only - safe, the sim drone really flies. #
  #  REAL HW: this commands a REAL TAKEOFF. PROPELLERS OFF on  #
  #  the bench, or the drone WILL try to fly. Keep clear.      #
  ##############################################################

USAGE (on whatever machine can reach the autopilot/SITL):
    python test_auto_to_guided.py  [connection] [baud]

Config keys used from config.py:
    MAVLINK_CONNECTION, MAVLINK_BAUD
    ANALYSIS_CLIMB_M   (default 5)   - how many metres to climb on fire
    TAKEOFF_ALT_M      (default 10)  - auto-takeoff altitude
    FIRE_HOVER_SEC     (default 30)  - seconds to hover before auto-resume
    HOVER_MODE         (default "GUIDED")
    CRUISE_MODE        (default "AUTO")
"""

import sys
import time
import select
import termios
import tty
from pymavlink import mavutil

import config

# ── tuneable constants ────────────────────────────────────────────────────────
CLIMB_M        = getattr(config, "ANALYSIS_CLIMB_M",  5)
FIRE_HOVER_SEC = getattr(config, "FIRE_HOVER_SEC",   10)   # auto-resume after this
GUIDED_MODE    = getattr(config, "HOVER_MODE",    "GUIDED")
AUTO_MODE      = getattr(config, "CRUISE_MODE",   "AUTO")
# ─────────────────────────────────────────────────────────────────────────────


# ── terminal helpers ──────────────────────────────────────────────────────────
def read_key():
    """Non-blocking single keypress (lowercase) or None.
    Works in cbreak mode (no ENTER needed) and in normal line mode."""
    if select.select([sys.stdin], [], [], 0)[0]:
        ch = sys.stdin.read(1)
        if ch:
            return ch.lower()
    return None


def is_vehicle_hb(master, msg):
    return (msg.get_srcSystem() == master.target_system
            and msg.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID)


# ── MAVLink helpers ───────────────────────────────────────────────────────────
def heartbeat(master, timeout=5):
    """Return (mode_str, armed) from the vehicle heartbeat."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        msg = master.recv_match(type="HEARTBEAT", blocking=True, timeout=timeout)
        if msg is None:
            return None, None
        if is_vehicle_hb(master, msg):
            return (mavutil.mode_string_v10(msg),
                    bool(msg.base_mode &
                         mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED))
    return None, None


def get_alt(master, timeout=2):
    """Latest relative altitude (m) or None."""
    msg = master.recv_match(type="GLOBAL_POSITION_INT",
                            blocking=True, timeout=timeout)
    return msg.relative_alt / 1000.0 if msg is not None else None


def request_streams(master, hz=4):
    """Ask autopilot to stream telemetry (needed on secondary TCP/UDP links)."""
    master.mav.request_data_stream_send(
        master.target_system, master.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_ALL, hz, 1)


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
    if name.upper() not in mapping:
        print(f"   '{name}' not in mode map. Known: {list(mapping)}")
        return False
    master.mav.set_mode_send(
        master.target_system,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        mapping[name.upper()])
    return True


def goto(master, lat, lon, alt_m):
    """Send GUIDED position target at relative/AGL altitude."""
    master.mav.set_position_target_global_int_send(
        0, master.target_system, master.target_component,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,          # position bits only
        int(lat * 1e7), int(lon * 1e7), float(alt_m),
        0, 0, 0, 0, 0, 0, 0, 0)


# ── arm + hand to AUTO ───────────────────────────────────────────────────────
def arm_and_start(master):
    """Arm in GUIDED (permissive arming mode), then immediately switch to AUTO.
    The mission's own TAKEOFF waypoint handles the climb — we never command
    a raw NAV_TAKEOFF altitude here."""
    print(f"[STEP2] arming in {GUIDED_MODE} ...")
    if not set_mode(master, GUIDED_MODE):
        return False
    time.sleep(1.0)

    _, armed = heartbeat(master)
    if not armed:
        send_arm(master, True)
        if not wait_armed(master, True, timeout=10):
            print("[STEP2] ARM failed "
                  "(check GPS/EKF, safety switch, pre-arm in MP).")
            return False

    print(f"[STEP2] armed -> switching to {AUTO_MODE} (mission TAKEOFF lifts off) ...")
    set_mode(master, AUTO_MODE)
    master.mav.command_long_send(
        master.target_system, master.target_component,
        mavutil.mavlink.MAV_CMD_MISSION_START, 0,
        0, 0, 0, 0, 0, 0, 0)
    return True


def land_and_disarm(master):
    """Safe shutdown: LAND if airborne, then disarm."""
    _, armed = heartbeat(master, timeout=3)
    a = get_alt(master) or 0.0
    if armed and a > 1.0:
        print(f"[STEP2] airborne ({a:.1f} m) -> LAND ...")
        set_mode(master, "LAND")
        t0 = time.time()
        while time.time() - t0 < 90:
            _, armed = heartbeat(master, timeout=3)
            if armed is False:
                print("[STEP2] landed + disarmed.")
                return
        print("[STEP2] land timeout - sending disarm anyway.")
    print("[STEP2] disarming ...")
    try:
        send_arm(master, False)
        wait_armed(master, False, timeout=10)
    except Exception as e:
        print("[STEP2] disarm error:", e)


# ── fire reaction ─────────────────────────────────────────────────────────────
def start_fire_reaction(master, lat, lon, alt):
    """
    Switch to GUIDED, climb CLIMB_M above current altitude, and return
    the timestamp at which we should auto-resume AUTO.

    Returns: (cruise_alt, resume_at_timestamp)
    """
    cruise_alt  = alt
    target_alt  = alt + CLIMB_M
    resume_at   = time.time() + FIRE_HOVER_SEC

    set_mode(master, GUIDED_MODE)
    goto(master, lat, lon, target_alt)

    print(f"   FIRE -> GUIDED | climb {alt:.1f} -> {target_alt:.1f} m "
          f"| hover {FIRE_HOVER_SEC}s then AUTO resumes automatically")
    print(f"   (press 'f' again to reset the {FIRE_HOVER_SEC}s timer)")

    return cruise_alt, resume_at


def resume_auto(master, lat, lon, cruise_alt):
    """Descend back to cruise altitude then switch to AUTO."""
    print(f"   RESUME -> descend to {cruise_alt:.1f} m, then AUTO")
    goto(master, lat, lon, cruise_alt)
    time.sleep(0.5)           # brief pause so the descent command is accepted
    set_mode(master, AUTO_MODE)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    conn = sys.argv[1] if len(sys.argv) > 1 else config.MAVLINK_CONNECTION
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else config.MAVLINK_BAUD

    print(f"[STEP2] connecting: {conn} (baud={baud} if serial)")
    if conn.startswith(("udp:", "tcp:", "udpin:")):
        master = mavutil.mavlink_connection(conn)
    else:
        master = mavutil.mavlink_connection(conn, baud=baud)

    print("[STEP2] waiting for heartbeat ...")
    master.wait_heartbeat()
    print(f"[STEP2] heartbeat OK -> system {master.target_system}")

    request_streams(master, hz=4)

    mode, armed = heartbeat(master)
    print(f"[STEP2] current: mode={mode}  armed={armed}")
    if armed:
        print("[STEP2] already armed - disarm from RC/MP first, then rerun.")
        master.close()
        return

    old_term = None
    try:
        print("\n>>> SITL = safe sim. REAL HW = REAL TAKEOFF - PROPS OFF on bench! <<<")
        input(">>> Press ENTER to ARM + TAKEOFF automatically (Ctrl-C to abort): ")

        # Put terminal in cbreak so f/q register immediately (no ENTER needed)
        if sys.stdin.isatty():
            old_term = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())

        # ── 1) arm in GUIDED, then hand off to AUTO ───────────────────────────
        if not arm_and_start(master):
            return

        t0 = time.time()
        while time.time() - t0 < 6:
            m, _ = heartbeat(master)
            if m == AUTO_MODE:
                print("[STEP2] AUTO confirmed - flying MP mission.")
                break
        else:
            print("[STEP2] AUTO not confirmed yet - continuing anyway.")

        print(f"\n[STEP2] RUNNING.  Keys: "
              f"f = FIRE (GUIDED climb {CLIMB_M}m for {FIRE_HOVER_SEC}s, then auto-resume)   "
              f"q = LAND + quit\n")

        # ── telemetry state ───────────────────────────────────────────────────
        mode = armed = lat = lon = alt = None
        seq        = None
        phase      = "AUTO"       # "AUTO" | "GUIDED"
        cruise_alt = None
        resume_at  = None         # float timestamp; when to auto-resume AUTO
        last_print = 0.0

        while True:
            # ── drain incoming MAVLink messages ───────────────────────────────
            msg = master.recv_match(blocking=True, timeout=0.1)
            if msg is not None:
                t = msg.get_type()
                if t == "HEARTBEAT" and is_vehicle_hb(master, msg):
                    mode  = mavutil.mode_string_v10(msg)
                    armed = bool(msg.base_mode &
                                 mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                elif t == "GLOBAL_POSITION_INT":
                    lat = msg.lat / 1e7
                    lon = msg.lon / 1e7
                    alt = msg.relative_alt / 1000.0
                elif t == "MISSION_CURRENT":
                    seq = msg.seq

            # ── key handling ──────────────────────────────────────────────────
            k = read_key()

            if k == "q":
                break

            elif k == "f":
                if lat is None:
                    print("   no GPS position yet - wait and try again")
                else:
                    # Always (re)start the fire reaction, whether in AUTO or
                    # already hovering. Pressing f again resets the timer.
                    cruise_alt, resume_at = start_fire_reaction(
                        master, lat, lon, alt)
                    phase = "GUIDED"

            # ── auto-resume after hover timer expires ─────────────────────────
            if phase == "GUIDED" and resume_at is not None:
                remaining = resume_at - time.time()
                if remaining <= 0 and lat is not None:
                    resume_auto(master, lat, lon, cruise_alt)
                    phase     = "AUTO"
                    resume_at = None

            # ── 1 Hz status line ──────────────────────────────────────────────
            now = time.time()
            if now - last_print >= 1.0:
                armed_txt = ("ARMED" if armed else "disarmed") if armed is not None else "?"
                pos_txt   = (f"alt {alt:+.1f}m  {lat:.6f},{lon:.6f}"
                             if lat is not None else "no position")
                timer_txt = ""
                if phase == "GUIDED" and resume_at is not None:
                    secs_left = max(0, resume_at - now)
                    timer_txt = f"  [auto-resume in {secs_left:.0f}s]"
                print(f"[{phase:<6}] mode={(mode or '?'):<9} {armed_txt:<9} "
                      f"wp={seq if seq is not None else '-'}  | {pos_txt}{timer_txt}")
                last_print = now

    except KeyboardInterrupt:
        print("\n[STEP2] Ctrl-C")
    finally:
        if old_term is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_term)
            except Exception:
                pass
        land_and_disarm(master)
        master.close()
        print("[STEP2] done.")


if __name__ == "__main__":
    main()