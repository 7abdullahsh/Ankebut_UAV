"""ANKEBUT - STEP 2 test: TAKEOFF + AUTO <-> GUIDED flight logic (SITL or bench).

FLIGHT ONLY (no models, no camera). Proves the flight-mode half of the fire
reaction using the SAME MAVLink commands flight_controller.py uses
(set_mode_send + set_position_target_global_int_send, relative-alt frame),
PLUS a hands-off auto-takeoff so AUTO can actually fly without manual throttle.

Flow when you press ENTER:
  1. switch to GUIDED, ARM, and NAV_TAKEOFF climb to TAKEOFF_ALT  (auto-takeoff)
  2. switch to AUTO  (flies the loaded mission, if any)
Then keys:
  'f' + ENTER  -> "FIRE": record current alt, GUIDED, climb +CLIMB_M
  'r' + ENTER  -> "RESUME": descend back to that alt, switch back to AUTO
  't' + ENTER  -> takeoff again (GUIDED climb to TAKEOFF_ALT) if needed
  'q' + ENTER  -> LAND (if airborne) then disarm, and quit

Why takeoff is needed: ArduCopter in AUTO will NOT lift off until throttle is
raised or a mission start is triggered. A GUIDED NAV_TAKEOFF climbs on its own,
which is what an autonomous mission start needs.

  ##############################################################
  #  SITL: simulation only - safe, the sim drone really flies. #
  #  REAL HW: this commands a REAL TAKEOFF. PROPELLERS OFF on   #
  #  the bench, or the drone WILL try to fly. Keep clear.       #
  ##############################################################

USAGE (on whatever machine can reach the autopilot/SITL):
    python test_auto_to_guided.py  [port] [baud]
"""

import sys
import time
import select
from pymavlink import mavutil

import config

CLIMB_M = getattr(config, "ANALYSIS_CLIMB_M", 1)
TAKEOFF_ALT = getattr(config, "TAKEOFF_ALT_M", 1)       # auto-takeoff altitude (m)
GUIDED_MODE = getattr(config, "HOVER_MODE", "GUIDED")
AUTO_MODE = getattr(config, "CRUISE_MODE", "AUTO")


def key_line():
    """Non-blocking read of one typed line (returns lowercase str or None)."""
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.readline().strip().lower()
    return None


def is_vehicle_hb(master, msg):
    """True only for the autopilot's heartbeat (ignore MP's GCS heartbeat)."""
    return (msg.get_srcSystem() == master.target_system
            and msg.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID)


def heartbeat(master, timeout=5):
    """Return (mode, armed) from the VEHICLE's heartbeat, skipping GCS ones."""
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
    """Ask the autopilot to send telemetry (position, etc.).

    A secondary link such as SITL TCP 5762 sends NOTHING until a client asks,
    so without this you get 'no position' forever. Mission Planner asks on its
    own link (5760); this script must ask on its link.
    """
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
        print(f"   '{name}' not available. Known: {list(mapping)}")
        return False
    master.mav.set_mode_send(
        master.target_system,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
        mapping[name.upper()])
    return True


def goto(master, lat, lon, alt_m):
    """GUIDED position target at relative/AGL altitude - identical to
    flight_controller.goto()."""
    master.mav.set_position_target_global_int_send(
        0, master.target_system, master.target_component,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        0b0000111111111000,                       # position only
        int(lat * 1e7), int(lon * 1e7), float(alt_m),
        0, 0, 0, 0, 0, 0, 0, 0)


def takeoff(master, alt_m, timeout=40):
    """Hands-off auto-takeoff: GUIDED + arm + NAV_TAKEOFF, wait for altitude.

    No manual throttle needed - this is how an autonomous mission starts.
    """
    print(f"[STEP2] TAKEOFF -> {GUIDED_MODE}, arm, climb to {alt_m} m")
    if not set_mode(master, GUIDED_MODE):
        return False
    time.sleep(1.0)

    _, armed = heartbeat(master)
    if not armed:
        send_arm(master, True)
        if not wait_armed(master, True, timeout=10):
            print("[STEP2] TAKEOFF aborted: could not arm "
                  "(check GPS/EKF, safety switch, pre-arm in MP).")
            return False

    # MAV_CMD_NAV_TAKEOFF: copter uses param7 as the (relative) takeoff altitude
    master.mav.command_long_send(
        master.target_system, master.target_component,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0,
        0, 0, 0, 0, 0, 0, float(alt_m))

    t0 = time.time()
    while time.time() - t0 < timeout:
        a = get_alt(master)
        if a is not None:
            print(f"   climbing ... alt {a:.1f} / {alt_m:.0f} m")
            if a >= alt_m * 0.95:
                print(f"[STEP2] TAKEOFF complete (~{a:.1f} m).")
                return True
    print("[STEP2] takeoff altitude not confirmed (timeout) - continuing.")
    return False


def land_and_disarm(master):
    """Safe shutdown: if airborne, LAND (copter auto-disarms once down);
    otherwise just disarm. Never force-disarm in the air."""
    _, armed = heartbeat(master, timeout=3)
    a = get_alt(master) or 0.0
    if armed and a > 1.0:
        print(f"[STEP2] airborne ({a:.1f} m) -> LAND (auto-disarms on touchdown) ...")
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


def main():
    conn = sys.argv[1] if len(sys.argv) > 1 else config.MAVLINK_CONNECTION
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else config.MAVLINK_BAUD

    print(f"[STEP2] connecting: {conn} (baud {baud} if serial)")
    if conn.startswith(("udp:", "tcp:", "udpin:")):
        master = mavutil.mavlink_connection(conn)
    else:
        master = mavutil.mavlink_connection(conn, baud=baud)
    print("[STEP2] waiting for heartbeat ...")
    master.wait_heartbeat()
    print(f"[STEP2] heartbeat OK -> system {master.target_system}")

    request_streams(master, hz=4)
    print("[STEP2] requested data streams (position should appear shortly)")

    mode, armed = heartbeat(master)
    print(f"[STEP2] current: mode={mode}  armed={armed}")
    if armed:
        print("[STEP2] already armed - disarm from RC/MP first, then rerun.")
        master.close()
        return

    try:
        print("\n>>> SITL = safe sim. REAL HW = this commands a REAL TAKEOFF - "
              "PROPS OFF on the bench! <<<")
        input(">>> Press ENTER to ARM + TAKEOFF (Ctrl-C to abort): ")

        # 1) hands-off auto-takeoff (AUTO alone won't lift off without throttle)
        takeoff(master, TAKEOFF_ALT)

        # 2) now airborne in GUIDED -> switch to AUTO to fly the mission
        print("[STEP2] switching to AUTO ...")
        set_mode(master, AUTO_MODE)
        # nudge the mission to start (helps if a mission with TAKEOFF is loaded;
        # harmless / rejected if there is no mission)
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_MISSION_START, 0,
            0, 0, 0, 0, 0, 0, 0)
        wait_mode_ok = False
        t0 = time.time()
        while time.time() - t0 < 6:
            m, _ = heartbeat(master)
            if m == AUTO_MODE:
                wait_mode_ok = True
                break
        if wait_mode_ok:
            print("[STEP2] AUTO reached - flying mission (if one is loaded).")
        else:
            print("[STEP2] AUTO not confirmed - staying airborne; "
                  "you can still test f/r.")

        print("\n[STEP2] keys:  f = FIRE (GUIDED + climb 5 m)   "
              "r = RESUME (descend + AUTO)   t = takeoff again   q = LAND + quit\n")

        mode = armed = lat = lon = alt = None
        seq = None
        cruise_alt = None
        phase = "AUTO"
        last_print = 0.0

        while True:
            msg = master.recv_match(blocking=True, timeout=0.2)
            if msg is not None:
                t = msg.get_type()
                if t == "HEARTBEAT":
                    if is_vehicle_hb(master, msg):     # ignore GCS heartbeat
                        mode = mavutil.mode_string_v10(msg)
                        armed = bool(msg.base_mode &
                                     mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                elif t == "GLOBAL_POSITION_INT":
                    lat = msg.lat / 1e7
                    lon = msg.lon / 1e7
                    alt = msg.relative_alt / 1000.0
                elif t == "MISSION_CURRENT":
                    seq = msg.seq

            k = key_line()
            if k == "q":
                break
            elif k == "t":
                takeoff(master, TAKEOFF_ALT)
                set_mode(master, AUTO_MODE)
                phase = "AUTO"
            elif k == "f":
                if phase != "AUTO":
                    print("   (already in GUIDED phase)")
                elif lat is None:
                    print("   no position yet - wait for GPS, then press f")
                else:
                    cruise_alt = alt
                    target = alt + CLIMB_M
                    set_mode(master, GUIDED_MODE)
                    goto(master, lat, lon, target)
                    phase = "GUIDED"
                    print(f"   FIRE -> GUIDED, climb {alt:.1f} -> {target:.1f} m "
                          f"(holding {lat:.6f},{lon:.6f})")
            elif k == "r":
                if phase != "GUIDED":
                    print("   (not in GUIDED phase)")
                else:
                    if lat is not None and cruise_alt is not None:
                        goto(master, lat, lon, cruise_alt)
                        print(f"   RESUME -> descend to {cruise_alt:.1f} m, then AUTO")
                    set_mode(master, AUTO_MODE)
                    phase = "AUTO"

            now = time.time()
            if now - last_print >= 1.0:
                armed_txt = (("ARMED" if armed else "disarmed")
                             if armed is not None else "?")
                pos_txt = (f"alt {alt:+.1f}m  {lat:.6f},{lon:.6f}"
                           if lat is not None else "no position")
                print(f"[{phase:<6}] mode={(mode or '?'):<9} {armed_txt:<9} "
                      f"wp={seq if seq is not None else '-'}  | {pos_txt}")
                last_print = now

    except KeyboardInterrupt:
        print("\n[STEP2] Ctrl-C")
    finally:
        land_and_disarm(master)
        master.close()
        print("[STEP2] done.")


if __name__ == "__main__":
    main()
