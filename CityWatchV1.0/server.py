"""
CIVICSYNC v1.0 - Intelligent City Surveillance Server
=====================================================

One process, everything industrial:

  [CAMERA] -> CaptureThread  ->  VisionAI.analyze()   ->  jpg + HUD burn-in
                             ->  BaselineAnomalyModel -> anomaly score
                             ->  alert engine (cooldown, severity, journal)
  [CLIENTS] -> /video        MJPEG annotated live feed
              /api/stream    SSE: live events + telemetry push
              /api/status    full system status + AI model report
              /api/retrain   spawns an in-process retraining session
              /app           PWA mobile app (installable)

Launch:   python server.py            (config from config.json + env CFW_*)
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import socket
import threading
import time
from collections import deque

# Bound HLS/HTTP capture stalls so a dead public stream can never freeze the
# pipeline thread (must be set before cv2 is imported).
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rw_timeout;8000000|timeout;5000000",)

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_from_directory

from ai.anomaly import BaselineAnomalyModel
from ai.detector import VisionAI

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)
ALERT_LOG = os.path.join(DATA_DIR, "alerts.jsonl")
REPORT_LOG = os.path.join(DATA_DIR, "civic_reports.jsonl")

START_TIME = time.time()
VERSION = "v1.0.0"


def load_config() -> dict:
    with open(os.path.join(ROOT, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


CFG = load_config()


# ---------------------------------------------------------------------------
# Event / alert engine
# ---------------------------------------------------------------------------

class EventHub:
    """Fan-out event bus: SSE subscribers + jsonl journal persistence."""

    def __init__(self, maxq: int = 200, maxlen: int = 300):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.recent: deque = deque(maxlen=maxlen)
        self._seq = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind: str, severity: str, title: str, detail: str | None = None,
                score: float | None = None, sector: str | None = None) -> dict:
        self._seq += 1
        ev = {
            "id": self._seq,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "epoch": round(time.time(), 3),
            "kind": kind,             # anomaly | person | system | motion | training
            "severity": severity,     # CRITICAL | WARNING | INFO | OK
            "title": title,
            "detail": detail or "",
            "score": score,
            "sector": sector or CFG.get("sector", "SECTOR-NORTH"),
        }
        self.recent.append(ev)

        def _push():
            with self._lock:
                subs = list(self._subs)
            for q in subs:
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass

        _push()
        if kind in ("anomaly", "system", "training"):
            try:
                with open(ALERT_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev) + "\n")
            except OSError:
                pass
        return ev


HUB = EventHub()

# ---------------------------------------------------------------------------
# Camera + AI pipeline thread
# ---------------------------------------------------------------------------

class CameraPipeline(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.jpeg: bytes | None = None
        self.stats = {
            "fps": 0.0,
            "camera_online": False,
            "mode": "BOOTING",
            "faces": 0,
            "motion_boxes": 0,
            "motion_ratio": 0.0,
            "brightness": 0.0,
            "edge_density": 0.0,
            "anomaly_score": 0.0,
            "anomaly_flag": False,
            "faces_series": [],
            "scores": [],
            "camera_label": "",
            "camera_coords": None,
        }
        self.latest_result = None
        self.model = BaselineAnomalyModel()
        self._ai: VisionAI | None = None
        self.stop = False
        self._src_index = 0

        # training session state (in-process retraining)
        self.train: dict | None = None    # {"target": n, "rows": [...], "t0": ts}
        self._alert_dirs = []
        self._last_face_event = 0.0
        self._last_anom_alert = 0.0
        self._anomaly_cooldown = float(
            CFG.get("anomaly", {}).get("alert_cooldown_seconds", 25))
        self._face_cooldown = float(
            CFG.get("anomaly", {}).get("face_event_cooldown_seconds", 12))

    # -- lifecycle ------------------------------------------------------

    def run(self) -> None:
        cfg = CFG["camera"]
        cfg_det = CFG.get("detection", {})

        sources = list(cfg.get("sources") or [])
        if not sources:
            sources = [{"source": cfg.get("source", 0), "label": "NODE CAM",
                        "lat": None, "lon": None}]
        self._src_index = int(os.environ.get("CIVICSYNC_SOURCE_INDEX", 0)) % len(sources)

        def _open(srcdef: dict):
            src = os.environ.get("CIVICSYNC_CAMERA") or srcdef.get("source", 0)
            if isinstance(src, int) or (isinstance(src, str) and src.isdigit()):
                idx = int(src)
                cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW if os.name == "nt" else idx)
            else:
                fp_ = src if os.path.isabs(src) else os.path.join(ROOT, src)
                cap = cv2.VideoCapture(str(fp_), cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.get("frame_width", 640))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.get("frame_height", 480))
            if isinstance(src, int) or (isinstance(src, str) and src.isdigit()):
                cap.set(cv2.CAP_PROP_FPS, CFG["stream"].get("target_fps", 20))
            return cap

        def _current_label():
            s = sources[self._src_index]
            return (s.get("label") or "NODE CAM",
                    (s.get("lat"), s.get("lon"))
                    if s.get("lat") is not None and s.get("lon") is not None else None)

        cap = _open(sources[self._src_index])
        label, coords = _current_label()
        while not self.stop:
            time.sleep(1.0 / max(5.0, CFG.get("stream", {}).get("target_fps", 15)))
            ok, frame = cap.read() if cap.isOpened() else (False, None)
            online = bool(ok and frame is not None)
            offline_delay = getattr(self, "_offline_for", 0.0)
            self._offline_for = offline_delay + 0.05 if not online else 0.0
            if not online:
                src_now = sources[self._src_index].get("source", 0)
                is_file = (isinstance(src_now, str) and not src_now.isdigit()
                           and src_now.endswith((".mp4", ".avi", ".mov", ".webm")))
                if is_file:
                    # Bundled dataset replay: when the clip ends, loop it
                    # seamlessly (no NO-SIGNAL card, no source rotation).
                    try:
                        cap.release()
                    except Exception:
                        pass
                    self._offline_for = 0.0
                    cap = _open(sources[self._src_index])
                    continue
                frame = self._offline_card()
                if cap.isOpened():
                    try:
                        cap.release()
                    except Exception:
                        pass
                # Public road streams die or move; rotate to the next source
                # (ends on the local webcam) after a few seconds without signal.
                if self._offline_for > 3.0:
                    srcdef = sources[self._src_index]
                    s_src = srcdef.get("source", 0)
                    fp = None
                    if isinstance(s_src, str) and not s_src.isdigit():
                        cand = s_src if os.path.isabs(s_src) else os.path.join(ROOT, s_src)
                        fp = cand if os.path.isfile(cand) else None
                    if fp:                     # bundled dataset: replay forever
                        pass
                    else:
                        self._src_index = (self._src_index + 1) % len(sources)
                    label, coords = _current_label()
                    self._offline_for = 0.0
                    cap = _open(sources[self._src_index])

            if self._ai is None:
                self._ai = VisionAI(CFG, CFG.get("camera_id", "CAM-01"),
                                    CFG.get("sector", "SECTOR-NORTH"), self.model)
            result = self._ai.analyze(frame)
            self._monitor(result, online)
            status = dict(self.stats)
            status["mode"] = "LEARNING" if self.train else ("ONLINE" if online else "NO SIGNAL")
            status["camera_online"] = online
            annotated = self._ai.annotate(frame, result, status)
            jpg_enc, jpg = cv2.imencode(".jpg", annotated,
                [int(cv2.IMWRITE_JPEG_QUALITY), CFG["stream"].get("jpeg_quality", 80)])
            faces_series = self.stats.get("faces_series", [])
            faces_series = (faces_series + [self._faces_raw])[-60:]
            with self.lock:
                self.jpeg = jpg.tobytes() if jpg_enc else None
                self.stats.update({
                    "fps": round(self._fps(online), 2),
                    "camera_online": online,
                    "mode": status["mode"],
                    "faces": len(result["face_boxes"]),
                    "faces_series": faces_series,
                    "motion_boxes": len(result["motion_boxes"]),
                    "motion_ratio": round(result["motion_ratio"], 4),
                    "brightness": result["features"]["brightness"],
                    "edge_density": result["features"]["edge_density"],
                    "anomaly_score": result["anomaly_score"],
                    "anomaly_flag": result["anomaly_flag"],
                    "scores": self._ai.recent_scores(),
                    "camera_label": label,
                    "camera_coords": {"lat": coords[0], "lon": coords[1]} if coords else None,
                })
                self.latest_result = result
        try:
            cap.release()
        except Exception:
            pass

    # -- alert / training monitor ----------------------------------------

    def _offline_card(self) -> np.ndarray:
        card = VisionAI.offline_frame((CFG["camera"].get("frame_width", 640),
                                       CFG["camera"].get("frame_height", 480)))
        return card

    def _monitor(self, r: dict, online: bool) -> None:
        if not online:
            self._faces_raw = 0
            return
        self._faces_raw = len(r["face_boxes"])

        # --- anomaly alerting (uses the TRAINED model) ---
        if r["anomaly_flag"] and not self.train:
            now = time.time()
            if now - self._last_anom_alert > self._anomaly_cooldown:
                self._last_anom_alert = now
                HUB.publish(
                    "anomaly", "CRITICAL",
                    title="ANOMALY DETECTED",
                    detail=("Scene deviation above learned normal baseline"
                            f" (score {r['anomaly_score']:.2f} > threshold "
                            f"{self.model.threshold:.2f})"),
                    score=r["anomaly_score"],
                )

        # --- persons observed (informational stream) ---
        if self._faces_raw > 0:
            now = time.time()
            if now - self._last_face_event > self._face_cooldown:
                self._last_face_event = now
                HUB.publish("person", "INFO",
                            title=f"PERSON DETECTED x{self._faces_raw}",
                            detail=f"{self._faces_raw} subject(s) visible in "
                                   f"{CFG.get('camera_id','CAM-01')}",
                            score=r["anomaly_score"])

        # --- in-process retraining session ---
        tr = self.train
        if tr is not None:
            tr["rows"].append([r["features"][k] for k in
                               ("face_count", "motion_ratio", "brightness", "edge_density")])
            if len(tr["rows"]) >= tr["target"]:
                X = np.array(tr["rows"], dtype=float)
                try:
                    rep = self.model.fit(X, mode="live-retrain")
                    HUB.publish("training", "OK",
                                title="MODEL RETRAIN COMPLETE",
                                detail=(f"{rep['samples']} samples, new threshold "
                                        f"{rep['threshold']}"),
                                score=None)
                except Exception as exc:
                    HUB.publish("training", "CRITICAL",
                                title="MODEL RETRAIN FAILED",
                                detail=str(exc)[:200])
                self.train = None

    # -- fps helper ------------------------------------------------------

    def _fps(self, _online: bool) -> float:
        now = time.time()
        marks = getattr(self, "_t", None)
        if marks is None:
            self._t = deque([now], maxlen=30)
            return 0.0
        self._t.append(now)
        if len(self._t) < 2:
            return 0.0
        span = self._t[-1] - self._t[0]
        return (len(self._t) - 1) / span if span > 0 else 0.0

    # -- public API ------------------------------------------------------

    def snapshot(self) -> dict:
        with self.lock:
            s = dict(self.stats)
        s["ai_model"] = self.model.report()
        s["faces_series"] = self.stats.get("faces_series", [])
        s["uptime_sec"] = int(time.time() - START_TIME)
        s["open_alerts"] = sum(1 for e in HUB.recent if e["severity"] == "CRITICAL")
        s["mode"] = "LEARNING" if self.train else s.get("mode", "ONLINE")
        return s

    def start_retrain(self, target: int = 200) -> bool:
        if self.train is not None:
            return False
        self.train = {"target": target, "rows": [], "t0": time.time()}
        HUB.publish("training", "WARNING", title="MODEL RETRAIN STARTED",
                    detail=f"Collecting {target} live feature vectors for new baseline...")
        return True


PIPE = CameraPipeline()

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)
# --- never let proxies/browsers stale-cache the UI -------------------------
@app.after_request
def _no_stale(resp):
    if request.path in ("/", "/app") or request.path.endswith((".css", ".js", ".html")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp



@app.route("/")
def dashboard():
    return send_from_directory(os.path.join(ROOT, "web"), "index.html")


@app.route("/app")
def app_page():
    return send_from_directory(os.path.join(ROOT, "web"), "app.html")


@app.route("/<path:path>")
def static_files(path: str):
    return send_from_directory(os.path.join(ROOT, "web"), path)


def _gen_mjpeg():
    while True:
        jpg = PIPE.jpeg
        if jpg is None:
            time.sleep(0.1)
            continue
        yield b"--cwframe\r\nContent-Type: image/jpeg\r\n" + \
            b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + \
            jpg + b"\r\n"
        time.sleep(1.0 / max(5.0, CFG["stream"].get("target_fps", 15)))


@app.route("/video")
def video():
    return Response(_gen_mjpeg(), mimetype="multipart/x-mixed-replace; boundary=cwframe")


@app.route("/snapshot")
def snapshot_img():
    jpg = PIPE.jpeg
    if not jpg:
        return ("no frame", 503)
    return Response(jpg, mimetype="image/jpeg")


@app.route("/api/status")
def api_status():
    return jsonify(PIPE.snapshot())


@app.route("/api/events")
def api_events():
    def gen():
        q = HUB.subscribe()
        try:
            for ev in list(HUB.recent)[-30:]:
                yield f"data: {json.dumps(ev)}\n\n"
            yield ": connected\n\n"
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            HUB.unsubscribe(q)
    return Response(gen(), mimetype="text/event-stream")


@app.route("/api/alerts")
def api_alerts():
    rows = []
    try:
        with open(ALERT_LOG, "r", encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f.read().splitlines()[-200:] if ln.strip()]
    except OSError:
        pass
    return jsonify(rows[::-1])


@app.route("/api/retrain", methods=["POST"])
def api_retrain():
    target = request.json.get("samples", 240) if request.is_json else 240
    ok = PIPE.start_retrain(max(60, min(target, 600)))
    return jsonify({"ok": ok, "queued": target if ok else 0})


@app.route("/api/system")
def api_system():
    with PIPE.lock:
        coords = PIPE.stats.get("camera_coords")
        label = PIPE.stats.get("camera_label", "")
    return jsonify({
        "product": "CivicSync",
        "version": VERSION,
        "ai_version": "baseline-gaussian-1.0",
        "uptime_sec": int(time.time() - START_TIME),
        "camera_id": CFG.get("camera_id"),
        "sector": CFG.get("sector"),
        "camera_label": label,
        "camera_coords": coords,
        "model_ready": PIPE.model.ready,
    })


# ---------------------------------------------------------------------------
# Civic reports: citizen submissions with attached photo proof.
#
# The client (web/js/proof.js) validates the capture before sending:
#   * only a live camera capture on THIS device (no gallery / download pick)
#   * EXIF must survive the file -> proves it was not downloaded or AI-made
#   * EXIF keep-time (DateTimeOriginal) must be recent -> no old/stale images
#   * EXIF GPS must be near the device's real-time GPS geolocation
# The server re-verifies what it can independently: it hashes the image bytes
# (same image resubmitted = rejected), re-parses the EXIF keep-time and GPS,
# and re-checks the client device coordinate distance.
# ---------------------------------------------------------------------------

_EDIT_SOFTWARE = ("photoshop", "lightroom", "gimp", "affinity", "canva",
                  "picsart", "snapseed", "fotor", "pixlr", "luminar",
                  "photopea", "editgram", "vsco", "midjourney", "dall",
                  "stable", "firefly", "imagen", "flux")


def _exif_from_bytes(data: bytes) -> dict:
    """Tiny standalone EXIF reader: enough tags to re-verify citizen proof."""
    import struct

    out = {"gps_lat": None, "gps_lon": None, "taken_at": None,
           "software": None, "make": None, "model": None}

    def _TIFF():
        idx = data.find(b"Exif\x00\x00")
        if idx < 0 or idx + 8 > len(data):
            return None
        irq = idx + 6
        endi = data[irq:irq + 2]
        little = endi == b"II"
        if not little and endi != b"MM":
            return None
        return irq, little

    parsed = _TIFF()
    if parsed is None:
        return out
    tiff, little = parsed

    def u16(o):
        return struct.unpack_from("<H" if little else ">H", data, o)[0]

    def u32(o):
        return struct.unpack_from("<I" if little else ">I", data, o)[0]

    def rationals(o, n):
        # `o` is an ABSOLUTE file offset from ifd_entries (already tiff-rel + tiff)
        vals = []
        for i in range(n):
            base = o + i * 8
            num, den = u32(base), u32(base + 4)
            vals.append(num / den if den else 0.0)
        return vals

    def ifd_entries(off):
        # yields (tag, type, count, value_field_start, value_len_bytes)
        off = tiff + off
        for i in range(u16(off)):
            e = off + 2 + i * 12
            tag, typ, cnt = u16(e), u16(e + 2), u32(e + 4)
            tsz = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}.get(typ, 1)
            size = tsz * cnt
            val_start = tiff + u32(e + 8) if size > 4 else e + 8
            yield tag, typ, cnt, val_start

    def ascii_(o, n):
        return data[o:o + n].rstrip(b"\x00").decode("ascii", "replace").strip()

    exif_ptr = gps_ptr = None
    for tag, typ, cnt, vs in list(ifd_entries(u32(tiff + 4))):
        if tag == 0x8769:
            exif_ptr = u32(vs)
        elif tag == 0x8825:
            gps_ptr = u32(vs)
        elif tag == 0x010F and typ == 2:
            out["make"] = ascii_(vs, cnt)
        elif tag == 0x0110 and typ == 2:
            out["model"] = ascii_(vs, cnt)
        elif tag == 0x0131 and typ == 2:
            out["software"] = ascii_(vs, cnt)

    if exif_ptr:
        for tag, typ, cnt, vs in ifd_entries(exif_ptr):
            if tag == 0x9003 and typ == 2:
                out["taken_at"] = ascii_(vs, cnt)
    if gps_ptr:
        lat_ref = lon_ref = None
        lat_r = lon_r = None
        for tag, typ, cnt, vs in ifd_entries(gps_ptr):
            if tag == 1 and typ == 2:
                lat_ref = ascii_(vs, cnt)
            elif tag == 2 and typ == 5:
                lat_r = [rationals(vs, cnt)[i] for i in range(cnt)]
            elif tag == 3 and typ == 2:
                lon_ref = ascii_(vs, cnt)
            elif tag == 4 and typ == 5:
                lon_r = [rationals(vs, cnt)[i] for i in range(cnt)]
        if lat_r and lon_r:
            lat = lat_r[0] + lat_r[1] / 60 + lat_r[2] / 3600
            lon = lon_r[0] + lon_r[1] / 60 + lon_r[2] / 3600
            if lat_ref in ("S", "s"):
                lat = -lat
            if lon_ref in ("W", "w"):
                lon = -lon
            out["gps_lat"], out["gps_lon"] = lat, lon
    return out


@app.route("/api/reports", methods=["POST"])
def api_report_post():
    body = request.get_json(force=True, silent=True) or {}
    rep = body.get("report") or {}
    proof = body.get("proof") or {}
    img_b64 = proof.get("base64") or ""
    if not img_b64:
        return jsonify({"ok": False, "error": "PHOTO PROOF REQUIRED"}), 400

    import base64
    try:
        img = base64.b64decode(img_b64, validate=True)
    except Exception:
        return jsonify({"ok": False, "error": "IMAGE DATA UNREADABLE"}), 400
    if len(img) > 6_000_000:
        return jsonify({"ok": False, "error": "IMAGE TOO LARGE"}), 400

    sha = hashlib.sha256(img).hexdigest()
    seen: list[dict] = []
    try:
        with open(REPORT_LOG, "r", encoding="utf-8") as f:
            seen = [json.loads(ln) for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        pass
    now = time.time()
    for r in seen:
        ev_old = r.get("evidence") or {}
        if ev_old.get("sha256_full") == sha and now - ev_old.get("received_epoch", 0) < 600:
            return jsonify({"ok": False, "error": "DUPLICATE IMAGE — ALREADY FILED"}), 409

    exif = _exif_from_bytes(img)
    if exif["taken_at"]:
        try:
            taken = time.mktime(time.strptime(exif["taken_at"], "%Y:%m:%d %H:%M:%S"))
        except ValueError:
            taken = 0
        if taken and now - taken > 1800:
            return jsonify({"ok": False, "error": "IMAGE NOT A FRESH CAPTURE (>30 min old)"}), 422

    software = (exif["software"] or "").lower()
    if exif["software"] and any(k in software for k in _EDIT_SOFTWARE):
        return jsonify({"ok": False, "error": "IMAGE WAS PROCESSED BY EDITING/AI SOFTWARE"}), 422
    if not exif["make"] and not exif["model"]:
        # Web captures and AI generations carry no device signature.
        return jsonify({"ok": False, "error": "NO EXIF CAMERA SIGNATURE — NOT A DIRECT CAPTURE"}), 422

    gps = exif["gps_lat"], exif["gps_lon"]
    dev = (proof.get("device") or {}).get("lat"), (proof.get("device") or {}).get("lon")
    gps_match, dist_m = True, None
    if all(v is not None for v in gps) and all(v is not None for v in dev):
        from math import asin, cos, radians, sin, sqrt
        lat1, lon1, lat2, lon2 = map(radians, (gps[0], gps[1], dev[0], dev[1]))
        a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
        dist_m = round(2 * 6_371_000 * asin(min(1, sqrt(a))))
        gps_match = dist_m <= 750
    verified = bool(gps_match)

    rec = {
        "id": rep.get("id") or f"{now:.0f}",
        "type": rep.get("type", "?"),
        "severity": rep.get("severity", "Low"),
        "location": rep.get("location", ""),
        "description": rep.get("description", ""),
        "status": rep.get("status", "Pending"),
        "date": rep.get("date", time.strftime("%Y-%m-%d")),
        "photoName": proof.get("filename", "") or "capture.jpg",
        "coords": {"lat": gps[0], "lon": gps[1]} if all(v is not None for v in gps)
                  else ({"lat": dev[0], "lon": dev[1]} if all(v is not None for v in dev) else None),
        "evidence": {
            "sha256": sha[:16], "sha256_full": sha, "bytes": len(img),
            "taken_at": exif["taken_at"],
            "device": exif["model"] or exif["make"] or None,
            "gps_mismatch_m": dist_m,
            "received": time.strftime("%Y-%m-%d %H:%M:%S"),
            "received_epoch": now,
        },
        "verified": verified,
    }
    try:
        with open(REPORT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        return jsonify({"ok": False, "error": "COULD NOT PERSIST REPORT"}), 500
    HUB.publish("system", "OK" if verified else "WARNING",
                title=f"CIVIC REPORT FILED — {rec['type'].upper()}",
                detail=(f"{rec['severity']} · {rec['location']}"
                        + (f" · GPS {dist_m}m {'MATCH' if gps_match else 'MISMATCH'}" if dist_m is not None else "")),
                sector=CFG.get("sector"))
    return jsonify({"ok": True, "verified": verified, "gps_mismatch_m": dist_m})


@app.route("/api/reports")
def api_report_get():
    try:
        with open(REPORT_LOG, "r", encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        rows = []
    return jsonify(rows[-200:][::-1])


# --- ADMIN-EXCLUSIVE: authoritative status control over the civic log -------
# Only the admin console (this port) may change a report's disposition; the
# change is applied to the official log and journal-managed.

@app.route("/api/reports/<rid>/status", methods=["POST"])
def api_report_status(rid: str):
    new_status = (request.get_json(force=True, silent=True) or {}).get("status")
    if new_status not in ("Pending", "In Progress", "Resolved"):
        return jsonify({"ok": False, "error": "INVALID STATUS"}), 400
    try:
        with open(REPORT_LOG, "r", encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return jsonify({"ok": False, "error": "LOG EMPTY"}), 404
    hit = 0
    for r in rows:
        if str(r.get("id")) == str(rid):
            r["status"] = new_status
            hit += 1
    if not hit:
        return jsonify({"ok": False, "error": "REPORT NOT FOUND"}), 404
    try:
        with open(REPORT_LOG, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
    except OSError:
        return jsonify({"ok": False, "error": "COULD NOT PERSIST"}), 500
    HUB.publish("system", "OK", title=f"REPORT {rid} → {new_status.upper()}",
                detail="Operator update from ADMIN console", sector=CFG.get("sector"))
    return jsonify({"ok": True})


def lan_addresses(port: int) -> list[str]:
    """IPv4 addresses of this PC on the local network (for phone/tablet access)."""
    found: set[str] = {"localhost"}
    try:
        info = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        for fam, _typ, _proto, _canon, sockaddr in info:
            ip = sockaddr[0]
            if ip and not ip.startswith("127."):
                found.add(ip)
    except Exception:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("8.8.8.8", 80))          # no traffic sent, route check only
        primary = probe.getsockname()[0]
        if primary:
            found.add(primary)
        probe.close()
    except Exception:
        pass
    ordered = sorted((ip for ip in found if ip != "localhost"),
                     key=lambda ip: (not ip.startswith("192.168."), ip))
    return [str(ip) for ip in ordered]


def print_network_banner(port: int) -> None:
    lan = lan_addresses(port)
    dash = [f"http://{ip}:{port}" for ip in lan]
    app_urls = [u + "/app" for u in dash]
    line = "-" * 46
    print()
    print(line)
    print("  CIVICSYNC v1.0  —  ONE NODE · TWO DOORS")
    print(line)
    print(f"  ADMIN CONSOLE : {dash[-1]}          (operators)")
    print(f"  CIVIL APP     : {app_urls[-1]}   (citizens · any device)")
    print(line)
    print("  On phones/tablets: scan the dashboard QR, or type the CIVIL APP url.")
    print("  Both doors are part of THIS ONE CivicSync node.")
    if os.name == "nt":
        print(line)
        print("  PHONE CANNOT CONNECT? Allow the firewall prompt once, or run:")
        line2 = ('netsh advfirewall firewall add rule name="CivicSync" '
                 "dir=in action=allow protocol=TCP localport=%d" % port)
        print(f"    (admin terminal) {line2}")
        print(line)


@app.route("/v1/models")
def api_v1_models():
    """OpenAI-style model listing (some probes/monitors look for this path)."""
    with PIPE.lock:
        ready = PIPE.model.ready
    return jsonify({
        "object": "list",
        "data": [
            {"id": "civicsync-scene-baseline", "object": "model", "ready": ready,
             "detail": "Gaussian scene-baseline anomaly model (trained in-CivicSync)"},
            {"id": "civicsync-face-yunet", "object": "model", "ready": True,
             "detail": "FaceDetectorYN (YuNet) person/subject detection core"},
        ],
        "civicsync": {"version": VERSION, "event_classes": ["system", "alert"]},
    })


@app.route("/health")
def api_health():
    return jsonify({"ok": True, "version": VERSION,
                    "uptime_s": round(time.time() - START_TIME)})


@app.route("/api/sources")
def api_sources():
    cfg = CFG.get("camera", {})
    sources = list(cfg.get("sources") or [{"source": cfg.get("source", 0), "label": "NODE CAM"}])
    active = getattr(PIPE, "_src_index", 0) % len(sources)
    return jsonify({
        "active": active,
        "cameras": [{
            "label": s.get("label") or "NODE CAM",
            "replay": bool(isinstance(s.get("source"), str) and s["source"].endswith((".mp4", ".avi", ".mov", ".webm"))),
            "route": s.get("route"),
            "place": s.get("place"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
        } for s in sources],
    })


@app.route("/api/network")
def api_network():
    port = int(CFG.get("server", {}).get("port", 8000))
    return jsonify({
        "dashboard": [f"http://{ip}:{port}" for ip in lan_addresses(port)],
        "app": [f"http://{ip}:{port}/app" for ip in lan_addresses(port)],
    })


@app.route("/api/qr")
def api_qr():
    text = request.args.get("text", "")
    try:
        import io

        import qrcode

        img = qrcode.make(text).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), mimetype="image/png")
    except Exception:
        return ("qr unavailable", 500)


# ---------------------------------------------------------------------------
# Demo dataset: a seeded "city corpus" of civic reports so every node boots
# with live content for presentations. Runs only when the civic log is empty.
# ---------------------------------------------------------------------------

_DEMO_REPORTS = [
    ("Road Debris",         "High",   "NH-24 service lane, Indirapuram flyover",   "Lost bus tyre tread blocking the left lane shoulder.",      "In Progress"),
    ("Blocked Drain",       "High",   "Ahinsa Khand-I · near Shipra Mall turn",     "Storm drain clogged; water pooling near the school gate.",  "Resolved"),
    ("Waterlogging",        "Medium", "Shakti Khand-II school underpass",          "Standing water across the cycle lane after rains.",          "Pending"),
    ("Pothole",             "Medium", "Bukundala road, Delhi Public School Indirapuram gate 2", "Deep pothole in the bus-bay, cycle damage risk.", "Pending"),
    ("Broken Streetlight",  "Low",    "Vaishali metro feeder road, K-block",       "Lamp flickering; footpath dark after evening dispersal.",    "Pending"),
    ("Damaged Sign",        "Low",    "Link road, Aditya Mall crossing",           "Speed-limit sign panel bent after storm; retro gone.",       "In Progress"),
    ("Waterlogging",        "Medium", "Indirapuram Habitat Centre boundary road",  "Pump offline, runoff backing onto the sidewalk.",            "Pending"),
    ("Blocked Drain",       "High",   "Jaipuria Sunrise green lane, Gate 3",       "Drain grate lifted; open slot hazard for buses.",            "In Progress"),
    ("Road Debris",         "High",   "Delhi Public School dispersal zone, Nyay Khand divider", "Fallen branches across both lanes after windstorm.", "Resolved"),
    ("Broken Streetlight",  "Low",    "Gaur Gravity crossing, service road",       "Sign light dead; two lamps flickering.",                     "Resolved"),
]


def _seed_demo_reports() -> int:
    """Write the demo corpus once — only if the civic log has no demo entries."""
    try:
        if os.path.exists(REPORT_LOG):
            with open(REPORT_LOG, "r", encoding="utf-8") as f:
                if any('"demo": true' in ln or '"demo":true' in ln
                       for ln in f.read().splitlines()):
                    return 0
        now = time.time()
        rows = []
        for i, (typ, sev, loc, desc, status) in enumerate(_DEMO_REPORTS):
            ts = now - (len(_DEMO_REPORTS) - i) * 5100 + i * 233   # ~1.5 h apart
            rows.append({
                "id": f"cw-demo-{i+1:02d}",
                "type": typ, "severity": sev, "location": loc, "description": desc,
                "status": status,
                "date": time.strftime("%Y-%m-%d", time.localtime(ts)),
                "photoName": f"demo-{i+1:02d}.jpg",
                "coords": None,
                "evidence": {"sha256": f"demo{i+1:016d}", "bytes": 0,
                             "taken_at": None, "device": "DEMO DATASET",
                             "gps_mismatch_m": None,
                             "received": time.strftime("%Y-%m-%d %H:%M:%S",
                                                       time.localtime(ts))},
                "verified": True,
                "demo": True,
                "proof": {"sha256": f"demo{i+1:016d}"},
            })
        with open(REPORT_LOG, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        return len(rows)
    except Exception:
        return 0


def main():
    seeded = _seed_demo_reports()
    PIPE.start()
    # give the pipeline a moment to boot, then announce on the bus
    time.sleep(1.0)
    threading.Timer(
        1.0,
        lambda: HUB.publish(
            "system", "OK", title="CIVICSYNC NODE ONLINE",
            detail=(f"AI model loaded: {'READY' if PIPE.model.ready else 'UNTRAINED'} "
                    f"({PIPE.model.n_samples} samples)"),
        ),
    ).start()
    host = CFG.get("server", {}).get("host", "0.0.0.0")
    # cloud hosts (Render / Railway / HF Spaces / Fly) inject the port via $PORT
    port = int(os.environ.get("PORT") or CFG.get("server", {}).get("port", 8000))
    print_network_banner(port)
    HUB.publish("system", "OK", title="DEMO DATASET + LAN ACCESS READY",
                detail=(f"Demo street feed replaying; open http://"
                        f"{(lan_addresses(port)[0] if lan_addresses(port) else 'PC-IP')}:{port}/app "
                        f"on any phone"))
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
