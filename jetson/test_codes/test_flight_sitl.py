"""TEST (Step 6) - flight_controller.py against ArduCopter SITL.

NEVER run this against a real drone first. Prove it in simulation here.

It does ONLY safe things: connect, wait for GPS, print position, switch to
GUIDED, switch to LOITER. It does NOT arm, take off, or move the vehicle.

Set CONN to match how you started SITL:
  * SITL on the SAME machine (sim_vehicle.py)      -> "udp:127.0.0.1:14551"
  * Mission Planner SITL forwarding to this device -> "udpin:0.0.0.0:14550"

  python test_flight_sitl.py
"""

import time

from flight_controller import FlightController

CONN = "udp:127.0.0.1:14551"        # <-- change to match your SITL setup

fc = FlightController(CONN)
print(f"connecting to SITL at {CONN} ...")
fc.connect()

print("waiting for a GPS fix...")
pos = None
for _ in range(30):
    pos = fc.get_position()
    if pos is not None:
        break
    time.sleep(1)

if pos is None:
    print("No position yet - is SITL running and the GPS locked?")
else:
    print("position:", pos)

print("current mode:", fc.mode)

print("-> GUIDED")
fc.set_mode("GUIDED")
time.sleep(2)
print("   mode now:", fc.mode)

print("-> LOITER")
fc.set_mode("LOITER")
time.sleep(2)
print("   mode now:", fc.mode)

fc.close()
print("done - if both mode switches printed correctly, flight_controller works.")
