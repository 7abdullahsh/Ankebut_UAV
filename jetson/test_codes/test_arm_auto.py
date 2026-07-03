"""ANKEBUT - interactive ARM + AUTO from the Jetson (BENCH, props-off).

Flow:
  1. connect; show current mode / armed
  2. press ENTER   -> send ARM   (confirm propellers are OFF first)
  3. type 'o' ENTER -> switch to AUTO
  4. live-monitor mode / armed / waypoint / position until Ctrl-C
  5. on exit -> always DISARM (safety)

  ##############################################################
  #  BENCH USE ONLY - PROPELLERS REMOVED.                      #
  #  When armed (and especially in AUTO, where the autopilot   #
  #  tries to take off) the MOTORS WILL SPIN. Props off = safe #
  #  but keep hands clear. Disarm any time: Ctrl-C here, or    #
  #  RC / Mission Planner.                                     #
  ##############################################################

This script does NOT command takeoff or waypoints - in AUTO the autopilot runs
the uploaded mission itself. On the bench it cannot actually climb, so it will
sit on the takeoff item; that's expected.

USAGE (Jetson, ~/main):   python test_arm_auto.py  [port] [baud]
"""

import sys
import time
from pymavlink import mavutil

import config

AUTO_MODE = getattr(config, "CRUISE_MODE", "AUTO")


def heartbeat(master, timeout=5):
    msg = master.recv_match(type="HEARTBEAT", blocking=True, timeout=timeout)
    if msg is None:
        return None, None
    mode = mavutil.mode_string_v10(msg)
    armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
    return mode, armed


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
    print("\n[ARM_AUTO] monitoring (Ctrl-C to stop + disarm).\n")
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
    conn = sys.argv[1] if len(sys.argv) > 1 else config.MAVLINK_CONNECTION
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else config.MAVLINK_BAUD

    print(f"[ARM_AUTO] connecting: {conn} (baud {baud} if serial)")
    if conn.startswith(("udp:", "tcp:", "udpin:")):
        master = mavutil.mavlink_connection(conn)
    else:
        master = mavutil.mavlink_connection(conn, baud=baud)
    print("[ARM_AUTO] waiting for heartbeat ...")
    master.wait_heartbeat()
    print(f"[ARM_AUTO] heartbeat OK -> system {master.target_system}")

    mode, armed = heartbeat(master)
    print(f"[ARM_AUTO] current: mode={mode}  armed={armed}")
    if armed:
        print("[ARM_AUTO] already armed - disarm first from RC/MP, then rerun.")
        master.close()
        return

    try:
        print("\n>>> Confirm ALL PROPELLERS ARE REMOVED. <<<")
        input(">>> Press ENTER to ARM (Ctrl-C to abort): ")
        print("[ARM_AUTO] sending ARM ...")
        send_arm(master, True)
        if not wait_armed(master, True, timeout=10):
            print("[ARM_AUTO] ARM FAILED/timed out. Check: safety switch, "
                  "battery, GPS/EKF, RC, pre-arm messages in Mission Planner.")
            return
        print("[ARM_AUTO] ARMED. (motors may idle-spin - props off, OK)")

        ans = input(f">>> Type 'o' + ENTER to switch to {AUTO_MODE} "
                    f"(anything else = stay, just monitor): ").strip().lower()
        if ans == "o":
            print(f"[ARM_AUTO] switching to {AUTO_MODE} "
                  f"(motors will spin up to 'take off' - props off, OK) ...")
            if set_mode(master, AUTO_MODE) and wait_mode(master, AUTO_MODE, 8):
                print(f"[ARM_AUTO] now in {AUTO_MODE}.")
            else:
                print(f"[ARM_AUTO] did NOT reach {AUTO_MODE} "
                      f"(needs GPS + an uploaded mission). Monitoring anyway.")
        else:
            print("[ARM_AUTO] staying in current mode. Monitoring.")

        monitor(master)

    except KeyboardInterrupt:
        print("\n[ARM_AUTO] Ctrl-C")
    finally:
        print("[ARM_AUTO] DISARMING (safety) ...")
        send_arm(master, False)
        wait_armed(master, False, timeout=10)
        master.close()
        print("[ARM_AUTO] done.")


if __name__ == "__main__":
    main()
