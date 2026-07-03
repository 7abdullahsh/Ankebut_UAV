"""ANKEBUT - read + monitor the AUTO mission from the Jetson (READ-ONLY).

What it does:
  1. Downloads the mission currently loaded on the Cube (the TAKEOFF + waypoints
     you planned in Mission Planner) and prints them -> proves the Jetson sees
     the SAME mission the autopilot will fly.
  2. Then live-monitors progress: flight mode, armed, current waypoint, distance
     to the next waypoint, altitude, position.

It NEVER arms, NEVER changes mode, NEVER uploads or edits the mission. It only
reads. Safe to run on the bench (props off) or as an observer during a real
flight. It does NOT fly the mission - the autopilot does that once you arm and
switch to AUTO (via RC or Mission Planner).

USAGE (on the Jetson, in ~/main):
    python test_mission_monitor.py
    python test_mission_monitor.py /dev/ttyTHS1 115200

Ctrl-C to stop.
"""

import sys
import math
import time
from pymavlink import mavutil

import config

# readable names for the mission commands you'll actually use
CMD_NAMES = {
    16: "WAYPOINT", 17: "LOITER_UNLIM", 19: "LOITER_TIME",
    20: "RTL", 21: "LAND", 22: "TAKEOFF", 82: "SPLINE_WP", 189: "DO_LAND_START",
}


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def download_mission(master, timeout=10):
    """Return {seq: (cmd, lat, lon, alt)} for the mission on the autopilot."""
    master.mav.mission_request_list_send(
        master.target_system, master.target_component)
    cnt = master.recv_match(type="MISSION_COUNT", blocking=True, timeout=timeout)
    if cnt is None:
        return None
    count = cnt.count
    items = {}
    for i in range(count):
        master.mav.mission_request_int_send(
            master.target_system, master.target_component, i)
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = master.recv_match(type="MISSION_ITEM_INT",
                                  blocking=True, timeout=timeout)
            if m is None:
                break
            if m.seq != i:
                continue
            lat = m.x / 1e7 if abs(m.x) > 1000 else float(m.x)
            lon = m.y / 1e7 if abs(m.y) > 1000 else float(m.y)
            items[i] = (m.command, lat, lon, m.z)
            break
    # tell the autopilot we're done so it stops waiting
    try:
        master.mav.mission_ack_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_MISSION_ACCEPTED)
    except Exception:
        pass
    return items


def main():
    conn = sys.argv[1] if len(sys.argv) > 1 else config.MAVLINK_CONNECTION
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else config.MAVLINK_BAUD

    print(f"[MISSION] connecting: {conn} (baud {baud} if serial)")
    if conn.startswith(("udp:", "tcp:", "udpin:")):
        master = mavutil.mavlink_connection(conn)
    else:
        master = mavutil.mavlink_connection(conn, baud=baud)
    print("[MISSION] waiting for heartbeat ...")
    master.wait_heartbeat()
    print(f"[MISSION] heartbeat OK -> system {master.target_system}\n")

    print("[MISSION] downloading mission from the autopilot ...")
    items = download_mission(master)
    if not items:
        print("[MISSION] no mission found (upload one from Mission Planner first).")
    else:
        print(f"[MISSION] {len(items)} item(s) on the Cube:")
        for seq in sorted(items):
            cmd, lat, lon, alt = items[seq]
            name = CMD_NAMES.get(cmd, f"cmd{cmd}")
            if name in ("TAKEOFF", "RTL", "LAND") and abs(lat) < 1e-6:
                print(f"   {seq:>2}: {name:<12} alt {alt:.1f}m")
            else:
                print(f"   {seq:>2}: {name:<12} {lat:.6f}, {lon:.6f}  alt {alt:.1f}m")
    print()

    # waypoints that have a real position, for distance-to-next
    wp_pos = {s: (la, lo) for s, (c, la, lo, al) in (items or {}).items()
              if abs(la) > 1e-6 or abs(lo) > 1e-6}

    print("[MISSION] live monitor (read-only). Ctrl-C to stop.\n")
    mode = armed = None
    lat = lon = alt = None
    seq = None
    last_print = 0.0
    try:
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
            if now - last_print >= 1.0:
                armed_txt = (("ARMED" if armed else "disarmed")
                             if armed is not None else "?")
                dist_txt = ""
                if seq in wp_pos and lat is not None:
                    d = haversine_m(lat, lon, wp_pos[seq][0], wp_pos[seq][1])
                    dist_txt = f"  dist->wp{seq} {d:6.1f}m"
                pos_txt = (f"alt {alt:+.1f}m  {lat:.6f},{lon:.6f}"
                           if lat is not None else "no position")
                print(f"mode={(mode or '?'):<9} {armed_txt:<9} "
                      f"wp={seq if seq is not None else '-'}{dist_txt}  | {pos_txt}")
                last_print = now
    except KeyboardInterrupt:
        print("\n[MISSION] stopped (read-only; nothing was commanded).")
    finally:
        master.close()


if __name__ == "__main__":
    main()
