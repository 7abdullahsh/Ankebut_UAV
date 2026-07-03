"""
ANKEBUT IHA - Drone MAVLink Simulator (Faz 2 Gun 3)
Sahte drone'un beyni. UDP uzerinden MAVLink mesajlari yayar:
  HEARTBEAT             @ 1 Hz   (bagli, hayatta sinyali)
  SYS_STATUS            @ 2 Hz   (batarya)
  GLOBAL_POSITION_INT   @ 5 Hz   (GPS)
  ATTITUDE              @ 10 Hz  (roll/pitch/yaw)
  VFR_HUD               @ 5 Hz   (hiz, irtifa, throttle)
"""
import math
import os
import sys
import time

# simulator/config.py'i bulabilmek icin
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

from pymavlink import mavutil
from pymavlink.dialects.v20 import common as mavlink2


# Sabitler
TICK_HZ = 50            # 20ms tick
TICK_DT = 1.0 / TICK_HZ

# Mesaj frekanslari (Hz)
HEARTBEAT_HZ  = 1
SYS_STATUS_HZ = 2
GLOBAL_POS_HZ = 5
ATTITUDE_HZ   = 10
VFR_HUD_HZ    = 5

# Baslangic GPS - Taksim
HOME_LAT = 41.0370
HOME_LON = 28.9857
HOME_ALT = 100.0

# Mod dongusu (her 30 sn'de bir degisir)
MODE_CYCLE = ['STABILIZE', 'AUTO', 'RTL']
MODE_CYCLE_PERIOD = 30.0

# ArduCopter custom mode numaralari
MODE_MAP = {
    'STABILIZE': 0, 'AUTO': 3, 'RTL': 6, 'LAND': 9, 'TAKEOFF': 13,
}


class DroneSimulator:
    def __init__(self, target_host: str, target_port: int):
        self.conn = mavutil.mavlink_connection(
            f'udpout:{target_host}:{target_port}',
            source_system=1,        # drone = system 1
            source_component=1,     # autopilot komponenti
            mavlink_version=2,
        )
        self.start_time = time.monotonic()
        self.running = False
        
        # Telemetri durumu (her tick'te guncellenir)
        self._state = {
            'armed': False,
            'mode': 'STABILIZE',
            'lat': HOME_LAT, 'lon': HOME_LON, 'alt': HOME_ALT,
            'heading': 90.0,
            'roll': 0.0, 'pitch': 0.0, 'yaw': math.radians(90.0),
            'airspeed': 0.0, 'groundspeed': 0.0,
            'climb': 0.0, 'throttle': 50,
            'battery_voltage': 12.6,   # 4S full
            'battery_pct': 100,
        }
        
        # Tick sayaclari
        self._tick = 0
        self._heartbeat_every  = TICK_HZ // HEARTBEAT_HZ
        self._sys_status_every = TICK_HZ // SYS_STATUS_HZ
        self._global_pos_every = TICK_HZ // GLOBAL_POS_HZ
        self._attitude_every   = TICK_HZ // ATTITUDE_HZ
        self._vfr_hud_every    = TICK_HZ // VFR_HUD_HZ
    
    def _elapsed(self) -> float:
        return time.monotonic() - self.start_time
    
    def _update_state(self):
        """Sahte fizik: zamana bagli sin/cos osilasyonlari."""
        t = self._elapsed()
        
        # Mod dongusu
        mode_idx = int(t / MODE_CYCLE_PERIOD) % len(MODE_CYCLE)
        self._state['mode'] = MODE_CYCLE[mode_idx]
        
        # Batarya: 100% -> 20% / 600 sn (10 dakikada)
        pct = max(20, 100 - (t / 600.0) * 80)
        self._state['battery_pct'] = int(pct)
        # Voltaj paralel: 12.6V (full) -> 10.4V (low)
        self._state['battery_voltage'] = 10.4 + (pct / 100.0) * 2.2
        
        # GPS: Taksim etrafinda dairesel
        radius_deg = 0.001  # ~111m
        self._state['lat'] = HOME_LAT + radius_deg * math.sin(t * 0.1)
        self._state['lon'] = HOME_LON + radius_deg * math.cos(t * 0.1)
        self._state['alt'] = HOME_ALT + 20 * math.sin(t * 0.05)
        self._state['heading'] = (math.degrees(t * 0.1) + 90) % 360
        
        # Attitude: hafif salinim
        self._state['roll']  = math.radians(15 * math.sin(t * 0.5))
        self._state['pitch'] = math.radians(10 * math.sin(t * 0.7))
        self._state['yaw']   = math.radians(self._state['heading'])
        
        # Hizlar
        self._state['airspeed']    = 5 + 2 * math.sin(t * 0.3)
        self._state['groundspeed'] = self._state['airspeed'] * 0.95
        self._state['climb']       = math.cos(t * 0.05)
        self._state['throttle']    = int(50 + 20 * math.sin(t * 0.4))
    
    def _send_heartbeat(self):
        base_mode = mavlink2.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        if self._state['armed']:
            base_mode |= mavlink2.MAV_MODE_FLAG_SAFETY_ARMED
        custom_mode = MODE_MAP.get(self._state['mode'], 0)
        
        self.conn.mav.heartbeat_send(
            type=mavlink2.MAV_TYPE_QUADROTOR,
            autopilot=mavlink2.MAV_AUTOPILOT_ARDUPILOTMEGA,
            base_mode=base_mode,
            custom_mode=custom_mode,
            system_status=mavlink2.MAV_STATE_ACTIVE,
        )
    
    def _send_sys_status(self):
        self.conn.mav.sys_status_send(
            onboard_control_sensors_present=0,
            onboard_control_sensors_enabled=0,
            onboard_control_sensors_health=0,
            load=500,                                          # %50 CPU (deci-percent)
            voltage_battery=int(self._state['battery_voltage'] * 1000),  # mV
            current_battery=2500,                              # 25A in cA
            battery_remaining=self._state['battery_pct'],
            drop_rate_comm=0, errors_comm=0,
            errors_count1=0, errors_count2=0, errors_count3=0, errors_count4=0,
        )
    
    def _send_global_position_int(self):
        hdg_rad = math.radians(self._state['heading'])
        gs = self._state['groundspeed']
        
        self.conn.mav.global_position_int_send(
            time_boot_ms=int(self._elapsed() * 1000),
            lat=int(self._state['lat'] * 1e7),
            lon=int(self._state['lon'] * 1e7),
            alt=int(self._state['alt'] * 1000),                       # mm
            relative_alt=int((self._state['alt'] - HOME_ALT) * 1000), # mm
            vx=int(gs * 100 * math.cos(hdg_rad)),                     # cm/s
            vy=int(gs * 100 * math.sin(hdg_rad)),                     # cm/s
            vz=int(-self._state['climb'] * 100),                      # NED: down +
            hdg=int(self._state['heading'] * 100),                    # cdeg
        )
    
    def _send_attitude(self):
        self.conn.mav.attitude_send(
            time_boot_ms=int(self._elapsed() * 1000),
            roll=self._state['roll'],
            pitch=self._state['pitch'],
            yaw=self._state['yaw'],
            rollspeed=0.0, pitchspeed=0.0, yawspeed=0.0,
        )
    
    def _send_vfr_hud(self):
        self.conn.mav.vfr_hud_send(
            airspeed=self._state['airspeed'],
            groundspeed=self._state['groundspeed'],
            heading=int(self._state['heading']),
            throttle=self._state['throttle'],
            alt=self._state['alt'],
            climb=self._state['climb'],
        )
    
    def _tick_once(self):
        self._update_state()
        if self._tick % self._heartbeat_every  == 0: self._send_heartbeat()
        if self._tick % self._sys_status_every == 0: self._send_sys_status()
        if self._tick % self._global_pos_every == 0: self._send_global_position_int()
        if self._tick % self._attitude_every   == 0: self._send_attitude()
        if self._tick % self._vfr_hud_every    == 0: self._send_vfr_hud()
        self._tick += 1
    
    def run(self):
        self.running = True
        next_tick = time.monotonic()
        last_log = 0.0
        
        while self.running:
            self._tick_once()
            
            # Her 5 saniyede bir konsola durum bas
            t = self._elapsed()
            if t - last_log >= 5.0:
                s = self._state
                print(f"[t={t:6.1f}s] mode={s['mode']:<10} bat={s['battery_pct']:3d}%  "
                      f"lat={s['lat']:.6f} lon={s['lon']:.6f} alt={s['alt']:6.1f}m  "
                      f"gs={s['groundspeed']:5.1f}m/s")
                last_log = t
            
            next_tick += TICK_DT
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.monotonic()
    
    def stop(self):
        self.running = False
        # Son bir disarmed heartbeat
        self._state['armed'] = False
        try:
            self._send_heartbeat()
        except Exception:
            pass


def main() -> int:
    target_host = config.TARGET_HOST
    target_port = config.MAVLINK_PORT
    
    print("=" * 60)
    print("ANKEBUT Drone MAVLink Simulator")
    print(f"  Hedef       : udp://{target_host}:{target_port}")
    print(f"  Heartbeat   : {HEARTBEAT_HZ} Hz")
    print(f"  Telemetri   : 5-10 Hz")
    print(f"  Baslangic   : Taksim ({HOME_LAT}, {HOME_LON})")
    print("=" * 60)
    print("Yayin basliyor... (Ctrl+C ile durdur)\n")
    
    sim = DroneSimulator(target_host, target_port)
    
    try:
        sim.run()
        return 0
    except KeyboardInterrupt:
        print("\n[!] Durduruluyor...")
        sim.stop()
        print("[+] Kapatildi.")
        return 0
    except Exception as e:
        print(f"[!] Hata: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())