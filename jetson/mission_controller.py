"""ANKEBUT Jetson - mission controller (the brain).

A non-blocking state machine. One loop, one frame at a time, never sleeps for
seconds (the 10 s analysis is a monotonic deadline checked each tick), so the
video stream to the GCS never stalls while the drone hovers.

Flow (matches the agreed flight logic):

  PATROL    AUTO, YOLO on. Watching for fire. Streams the annotated feed and
            light detection messages to the GCS.
    | fire detected (and in cruise mode)
    v
  LOCKED    lock current GPS, tell GCS, switch -> GUIDED, stop YOLO.
    |
    v
  CLIMBING  (optional) climb ANALYSIS_CLIMB_M for a wider FOV.
    |
    v
  ANALYZING segmentation on, wind fetched (off-thread), for ANALYSIS_DURATION_S.
    |       Then send the full fire_event (location + terrain + wind + risk).
    v
  DESCENDING(optional) back to cruise altitude.
    |
    v
  RESUMING  switch -> AUTO, restart YOLO.
    |
    v
  COOLDOWN  ignore re-triggers for DETECT_COOLDOWN_S, then -> PATROL.

  RTL       on mission complete / request: switch -> RTL, stop. (terminal)

The controller talks to its dependencies through small interfaces, so it can
be unit-tested with fakes (see the test that ships with this project).
"""

import threading
import time

CLIMB_TIMEOUT_S = 12        # give up waiting to reach climb/descend altitude


class MissionState:
    PATROL = "PATROL"
    CLIMBING = "CLIMBING"
    ANALYZING = "ANALYZING"
    DESCENDING = "DESCENDING"
    COOLDOWN = "COOLDOWN"
    RTL = "RTL"
    STOPPED = "STOPPED"


class MissionController:
    def __init__(self, camera, detector, segmenter, flight, wind, gcs,
                 video_sender, cfg):
        self.camera = camera
        self.detector = detector
        self.segmenter = segmenter
        self.flight = flight
        self.wind = wind
        self.gcs = gcs
        self.video = video_sender

        self.hover_mode = getattr(cfg, "HOVER_MODE", "GUIDED")
        self.cruise_mode = getattr(cfg, "CRUISE_MODE", "AUTO")
        self.return_mode = getattr(cfg, "RETURN_MODE", "RTL")
        self.analysis_duration = getattr(cfg, "ANALYSIS_DURATION_S", 10)
        self.analysis_climb = getattr(cfg, "ANALYSIS_CLIMB_M", 0)
        self.cooldown = getattr(cfg, "DETECT_COOLDOWN_S", 15)
        send_hz = getattr(cfg, "DETECTION_SEND_HZ", 10)
        self._send_interval = 1.0 / send_hz if send_hz > 0 else 0.0

        self.state = MissionState.PATROL
        self._running = False
        self._rtl_requested = False

        # per-event scratch
        self._locked_pos = None
        self._cruise_alt = 0.0
        self._climb_target = 0.0
        self._deadline = 0.0
        self._climb_deadline = 0.0
        self._last_terrain = self._empty_terrain()
        self._wind_result = None
        self._last_send = 0.0

    # ------------------------------------------------------------------
    @staticmethod
    def _empty_terrain():
        return {"tree": 0.0, "green_grass": 0.0, "dry_grass": 0.0,
                "water": 0.0, "soil": 0.0}

    def request_rtl(self):
        self._rtl_requested = True

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    def run(self):
        """Blocking loop. Run in the main thread; camera/video opened by main."""
        self._running = True
        self.detector.start()
        print("[MISSION] PATROL")
        while self._running:
            frame = self.camera.read()
            if frame is None:
                time.sleep(0.01)
                continue
            try:
                self._tick(frame)
            except Exception as e:
                print("[MISSION] tick error:", e)
        print("[MISSION] stopped")

    # ------------------------------------------------------------------
    def _tick(self, frame):
        now = time.monotonic()
        pos = self.flight.get_position()
        s = self.state

        if s == MissionState.PATROL:
            dets, annotated = self.detector.detect(frame)
            self.video.send(annotated)
            self._maybe_send("PATROL", dets, pos, now)

            if self._rtl_requested or self.flight.is_mission_complete():
                self._enter_rtl(pos)
                return

            cruise_ok = (self.flight.is_mode(self.cruise_mode)
                         or self.flight.mode is None)
            if self._has_fire(dets) and cruise_ok:
                self._on_fire(dets, pos, now)

        elif s == MissionState.CLIMBING:
            self.video.send(frame)
            self._maybe_send("CLIMBING", [], pos, now)
            if self.flight.at_altitude(self._climb_target) or now >= self._climb_deadline:
                self._begin_analysis(now)

        elif s == MissionState.ANALYZING:
            terrain, annotated = self.segmenter.segment(frame)
            if terrain and sum(terrain.values()) > 0:
                self._last_terrain = terrain
            self.video.send(annotated)
            remaining = max(0.0, self._deadline - now)
            self._maybe_send("ANALYZING", [], pos, now,
                             analyze_remaining_s=remaining)
            if now >= self._deadline:
                self._finish_analysis(pos, now)

        elif s == MissionState.DESCENDING:
            self.video.send(frame)
            self._maybe_send("DESCENDING", [], pos, now)
            if self.flight.at_altitude(self._cruise_alt) or now >= self._climb_deadline:
                self._resume_patrol(now)

        elif s == MissionState.COOLDOWN:
            dets, annotated = self.detector.detect(frame)
            self.video.send(annotated)
            # reported as PATROL on the GCS; triggers are simply ignored here
            self._maybe_send("PATROL", dets, pos, now)
            if now >= self._cooldown_until:
                self.state = MissionState.PATROL
                print("[MISSION] PATROL")

        elif s == MissionState.RTL:
            self.video.send(frame)

    # ------------------------------------------------------------------
    # transitions
    def _on_fire(self, dets, pos, now):
        print("[MISSION] FIRE -> lock + GUIDED")
        self._locked_pos = pos or self._locked_pos
        self.gcs.send_detection("LOCKED", dets, pos)      # one-shot, immediate
        self.flight.set_mode(self.hover_mode)
        self.detector.stop()
        self._cruise_alt = pos["alt_m"] if pos else 0.0
        self._wind_result = None
        self._start_wind_fetch(self._locked_pos)

        if self.analysis_climb > 0 and pos is not None:
            self._climb_target = self._cruise_alt + self.analysis_climb
            self.flight.climb_to(self._climb_target)
            self._climb_deadline = now + CLIMB_TIMEOUT_S
            self.state = MissionState.CLIMBING
            print("[MISSION] CLIMBING")
        else:
            self._begin_analysis(now)

    def _begin_analysis(self, now):
        self.segmenter.start()
        self._deadline = now + self.analysis_duration
        self._last_terrain = self._empty_terrain()
        self.state = MissionState.ANALYZING
        print("[MISSION] ANALYZING")

    def _finish_analysis(self, pos, now):
        risk = self._compute_risk(self._last_terrain, self._wind_result)
        fire_loc = {
            "lat": self._locked_pos["lat"] if self._locked_pos else None,
            "lon": self._locked_pos["lon"] if self._locked_pos else None,
            "alt_m": self._locked_pos["alt_m"] if self._locked_pos else None,
        }
        self.gcs.send_fire_event(fire_loc, self._last_terrain,
                                 self._wind_result, risk)
        self.segmenter.stop()
        print("[MISSION] event sent")

        if self.analysis_climb > 0 and pos is not None:
            self.flight.climb_to(self._cruise_alt)
            self._climb_deadline = now + CLIMB_TIMEOUT_S
            self.state = MissionState.DESCENDING
            print("[MISSION] DESCENDING")
        else:
            self._resume_patrol(now)

    def _resume_patrol(self, now):
        self.flight.set_mode(self.cruise_mode)
        self.detector.start()
        self._cooldown_until = now + self.cooldown
        self.gcs.send_detection("RESUMING", [], self.flight.get_position())
        self.state = MissionState.COOLDOWN
        print("[MISSION] RESUMING -> COOLDOWN")

    def _enter_rtl(self, pos):
        self.flight.set_mode(self.return_mode)
        self.detector.stop()
        self.gcs.send_detection("RTL", [], pos)
        self.state = MissionState.RTL
        self._running = False
        print("[MISSION] RTL")

    # ------------------------------------------------------------------
    # helpers
    def _has_fire(self, dets):
        return any(d.get("label") == "fire" for d in dets)

    def _maybe_send(self, state, dets, pos, now, analyze_remaining_s=None):
        if now - self._last_send >= self._send_interval:
            self.gcs.send_detection(state, dets, pos,
                                    analyze_remaining_s=analyze_remaining_s)
            self._last_send = now

    def _start_wind_fetch(self, pos):
        if pos is None:
            return

        def worker():
            self._wind_result = self.wind.get_wind(pos["lat"], pos["lon"])

        threading.Thread(target=worker, daemon=True).start()

    def _compute_risk(self, terrain, wind):
        dry = terrain.get("dry_grass", 0.0)
        tree = terrain.get("tree", 0.0)
        green = terrain.get("green_grass", 0.0)
        fuels = {"dry_grass": dry, "tree": tree, "green_grass": green}
        primary = max(fuels, key=fuels.get) if max(fuels.values()) > 0 else "soil"
        flammable = dry + tree + 0.5 * green
        speed = wind.get("speed_ms", 0.0) if wind else 0.0

        if dry >= 0.5 or (flammable >= 0.6 and speed >= 5):
            level = "HIGH"
        elif flammable >= 0.3:
            level = "MEDIUM"
        else:
            level = "LOW"

        note = f"{primary.replace('_', ' ')} {int(fuels.get(primary, 0) * 100)}% dominant"
        if wind:
            spread = (wind.get("direction_deg", 0) + 180) % 360
            note += (f"; wind {wind.get('direction', '?')} "
                     f"{wind.get('speed_ms', 0)} m/s (spread ~{spread} deg)")
        return {"level": level, "primary_fuel": primary, "note": note}
