"""Goruntu isleme ekibinin UDP'den yolladigi AI sonuclarini dinler.
Protocol kararlasacak (JSON onerilen), suanda iskelet halinde."""
import json
import socket
import time

from PySide6.QtCore import QObject, QThread, Signal


class _ReceiverThread(QThread):
    results_updated = Signal(dict)
    alarm_state_changed = Signal(bool)
    
    def __init__(self, port: int, parent=None):
        super().__init__(parent)
        self._port = port
        self._running = False
    
    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('0.0.0.0', self._port))
            sock.settimeout(0.5)
        except Exception as e:
            print(f"[AIReceiver] Bind hatasi: {e}")
            return
        
        print(f"[AIReceiver] udp://0.0.0.0:{self._port} dinleniyor...")
        self._running = True
        last_alarm = False
        
        while self._running and not self.isInterruptionRequested():
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[AIReceiver] Hata: {e}")
                continue
            
            try:
                msg = json.loads(data.decode('utf-8'))
            except Exception:
                continue
            
            # Beklenen protocol:
            # {
            #   "type": "detection",
            #   "target_lat": 41.123, "target_lon": 28.456,
            #   "distance_m": 52, "spread_angle": 135,
            #   "spread_text": "Guneydogu",
            #   "confidence": 87,
            #   "alarm": true
            # }
            self.results_updated.emit(msg)
            
            alarm = bool(msg.get('alarm', False))
            if alarm != last_alarm:
                self.alarm_state_changed.emit(alarm)
                last_alarm = alarm
        
        sock.close()


class AIResultsProvider(QObject):
    results_updated = Signal(dict)
    alarm_state_changed = Signal(bool)
    
    def __init__(self, udp_port: int = 14560, parent=None):
        super().__init__(parent)
        self._thread = _ReceiverThread(udp_port)
        self._thread.results_updated.connect(self.results_updated)
        self._thread.alarm_state_changed.connect(self.alarm_state_changed)
    
    def start(self):
        self._thread.start()
    
    def stop(self):
        self._thread.requestInterruption()
        self._thread.wait(2000)