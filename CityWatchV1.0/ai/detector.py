"""
CityWatch Vision AI - detection & feature extraction layer.

This module is the "eyes" of the system. It runs a hybrid AI pipeline:

  1. MOTION ANALYSIS .... MOG2 adaptive background subtraction (OpenCV 5)
     -> learns the scene's "empty state" live and segments moving objects.
  2. PERSON / FACE DETECT .. YuNet neural face detector (DNN, self-hosted ONNX)
  3. FEATURE ENCODER ...... converts every frame into a 4-D feature vector
     consumed by the trained baseline model in ai/anomaly.py.

All post-processing (burn-in overlays, timestamping, HUD) is drawn here so
the industrial dashboard receives a fully annotated single JPEG per frame.
"""

from __future__ import annotations

import os
import time

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# YuNet neural face detector (ships with this repo, ai/models/*.onnx)
# ---------------------------------------------------------------------------

YUNET_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "models", "face_detection_yunet_2023mar.onnx")


class FaceDetectorYNWrapper:
    """cv2.FaceDetectorYN (YuNet DNN) adapter with graceful degradation."""

    def __init__(self, score: float = 0.55, every: int = 3):
        self.ok = os.path.isfile(YUNET_PATH)
        self.every = max(1, int(every))
        self.det = None
        if self.ok:
            try:
                self.det = cv2.FaceDetectorYN.create(
                    YUNET_PATH, "", (640, 480), score, 0.3, 5000)
            except Exception as exc:
                print(f"[VisionAI] YuNet unavailable ({exc}) - faces disabled")
                self.ok = False
        self._n = 0
        self._last: list = []

    def detect(self, frame: np.ndarray) -> list:
        """Half-frame detect, throttled every N-th call, cached in between."""
        if not self.ok:
            return list(self._last)
        self._n += 1
        if self._n % self.every != 0:
            return list(self._last)
        h, w = frame.shape[:2]
        try:
            self.det.setInputSize((w, h))
            _ret, faces = self.det.detect(frame)
        except Exception:
            return list(self._last)
        if faces is not None and len(faces):
            self._last = [(int(f[0]), int(f[1]), int(f[2]), int(f[3]))
                          for f in faces[:8]]
        else:
            self._last = []
        return list(self._last)


FEATURE_NAMES = ["face_count", "motion_ratio", "brightness", "edge_density"]

_OVERLAY_FONT = cv2.FONT_HERSHEY_SIMPLEX

# HUD palette (drawn on the video feed - phosphor-industrial style)
_COL_OK = (46, 230, 168)       # green
_COL_WARN = (255, 176, 32)     # amber
_COL_CRIT = (255, 77, 77)      # red
_COL_INFO = (77, 163, 255)     # blue
_COL_DIM = (150, 160, 170)


class MotionEngine:
    """Adaptive background subtraction -> moving-object boxes + motion ratio."""

    def __init__(self, var_threshold: float = 30.0, min_area_ratio: float = 0.015):
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=420, varThreshold=var_threshold, detectShadows=True
        )
        self.min_area_ratio = min_area_ratio
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def reset(self) -> None:
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=420, varThreshold=32, detectShadows=True
        )

    def detect(self, frame: np.ndarray) -> dict:
        mask = self.bg.apply(frame)
        # strip soft shadows (value ~127) and keep real motion (255)
        mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)[1]
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel, iterations=1)
        mask = cv2.dilate(mask, self.kernel, iterations=2)
        h, w = mask.shape
        base_area = float(h * w)
        min_pix = max(300.0, base_area * self.min_area_ratio)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for cnt in contours:
            if cv2.contourArea(cnt) < min_pix:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bh > h * 0.92 and bw > w * 0.92:
                continue  # lighting flash fills whole frame -> ignore
            boxes.append((x, y, bw, bh))
        motion_ratio = float(np.count_nonzero(mask)) / base_area
        return {"mask": mask, "boxes": boxes, "motion_ratio": motion_ratio}


class VisionAI:
    """Frame analysis + HUD annotation. One instance per pipeline."""

    def __init__(self, cfg: dict, camera_id: str, sector: str, model):
        det = cfg.get("detection", {})
        self.motion = MotionEngine(
            var_threshold=float(det.get("mog2_var_threshold", 30)),
            min_area_ratio=float(det.get("min_motion_area_ratio", 0.015)),
        )
        self.faces = FaceDetectorYNWrapper(score=0.55)
        self.face_interval = int(det.get("face_detect_interval", 3))
        self.edge_scale = float(det.get("edge_sample_scale", 0.25))
        self.camera_id = camera_id
        self.sector = sector
        self.model = model          # BaselineAnomalyModel
        self._frame_no = 0
        self._recent_scores: list[float] = [0.0] * 40

    # ---------------- core analysis ----------------

    def analyze(self, frame: np.ndarray) -> dict:
        self._frame_no += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        m = self.motion.detect(frame)

        face_boxes: list[tuple[int, int, int, int]] = []
        if self.faces is not None and self.faces.ok:
            h, w = frame.shape[:2]
            half = cv2.resize(frame, (max(64, w // 2), max(48, h // 2)))
            raw = self.faces.detect(half)
            face_boxes = [(x * 2, y * 2, bw * 2, bh * 2) for (x, y, bw, bh) in raw]

        # edge density on a downscaled copy (cheap, stable)
        es = max(1, int(frame.shape[1] * self.edge_scale))
        eimg = cv2.resize(gray, (es, max(1, int(frame.shape[0] * self.edge_scale))))
        edges = cv2.Canny(eimg, 60, 160)
        edge_density = float(np.count_nonzero(edges)) / edges.size

        features = [
            float(len(face_boxes)),
            round(m["motion_ratio"], 5),
            round(float(gray.mean()) / 255.0, 4),
            round(edge_density, 5),
        ]
        anomaly = self.model or None
        score, is_anom = (0.0, False)
        if anomaly is not None and anomaly.ready:
            score, is_anom = anomaly.score(features)

        result = {
            "frame_no": self._frame_no,
            "ts": time.time(),
            "motion_boxes": m["boxes"],
            "motion_ratio": m["motion_ratio"],
            "face_boxes": face_boxes,
            "features": dict(zip(FEATURE_NAMES, features)),
            "anomaly_score": round(float(score), 3),
            "anomaly_flag": bool(is_anom),
        }
        self._push_score(score)
        return result

    def _push_score(self, score: float) -> None:
        self._recent_scores.append(float(score))
        self._recent_scores = self._recent_scores[-40:]

    def recent_scores(self) -> list[float]:
        return list(self._recent_scores)

    # ---------------- HUD annotation ----------------

    def annotate(self, frame: np.ndarray, r: dict, status: dict) -> np.ndarray:
        out = frame.copy()
        h, w = out.shape[:2]
        anom = r.get("anomaly_score", 0.0)
        col = _COL_CRIT if r.get("anomaly_flag") else _COL_OK

        # motion boxes (industrial dash style: corner ticks)
        for (x, y, bw, bh) in r["motion_boxes"]:
            cv2.rectangle(out, (x, y), (x + bw, y + bh), (90, 130, 60), 1)
            self._corner_ticks(out, (x, y, bw, bh), _COL_WARN, 14, 2)
            cv2.putText(out, "MOTION", (x, max(14, y - 6)), _OVERLAY_FONT,
                        0.42, _COL_WARN, 1, cv2.LINE_AA)

        # face boxes
        for (x, y, bw, bh) in r["face_boxes"]:
            self._corner_ticks(out, (x, y, bw, bh), _COL_INFO, 18, 2)
            cv2.putText(out, "PERSON", (x, max(14, y - 6)), _OVERLAY_FONT,
                        0.45, _COL_INFO, 1, cv2.LINE_AA)

        self._crosshair(out, w, h)
        self._burn_timestamp(out)
        self._top_bar(out, r, status)
        self._bottom_bar(out, r, status, col)
        self._rec_blink(out, r, w)
        return out

    @staticmethod
    def _corner_ticks(img, box, col, ln, th):
        x, y, bw, bh = box
        for (p1, p2) in [
            ((x, y), (x + ln, y)), ((x, y), (x, y + ln)),
            ((x + bw - ln, y), (x + bw, y)), ((x + bw, y), (x + bw, y + ln)),
            ((x, y + bh - ln), (x, y + bh)), ((x, y + bh), (x + ln, y + bh)),
            ((x + bw - ln, y + bh), (x + bw, y + bh)), ((x + bw, y + bh - ln), (x + bw, y + bh)),
        ]:
            cv2.line(img, p1, p2, col, th, cv2.LINE_AA)

    @staticmethod
    def _crosshair(img, w, h):
        m = 18
        for cx, cy in ((w // 2, m), (w // 2, h - m), (m, h // 2), (w - m, h // 2)):
            cv2.drawMarker(img, (cx, cy), _COL_DIM, cv2.MARKER_CROSS, 10, 1)

    def _burn_timestamp(self, out):
        h = out.shape[0]
        ts = time.strftime("%Y-%m-%d  %H:%M:%S")
        cv2.putText(out, ts, (10, h - 42), _OVERLAY_FONT, 0.5, (240, 240, 240), 1, cv2.LINE_AA)
        cv2.putText(out, f"{self.camera_id}  CCTV-RGB  H.264/MJPG", (10, h - 22),
                    _OVERLAY_FONT, 0.42, _COL_DIM, 1, cv2.LINE_AA)

    def _top_bar(self, out, r, status):
        txt = f"CITYWATCH-AI // {self.camera_id} // {self.sector}"
        cv2.rectangle(out, (0, 0), (out.shape[1], 26), (12, 14, 16), -1)
        cv2.putText(out, txt, (8, 18), _OVERLAY_FONT, 0.5, (200, 210, 220), 1, cv2.LINE_AA)
        s = "LEARNING" if status.get("mode") == "LEARNING" else (
            "ONLINE" if status.get("camera_online") else "NO SIGNAL")
        col = _COL_WARN if status.get("mode") == "LEARNING" else (_COL_OK if
            status.get("camera_online") else _COL_CRIT)
        cv2.putText(out, s, (out.shape[1] - 110, 18), _OVERLAY_FONT, 0.5, col, 1, cv2.LINE_AA)
        label = status.get("camera_label")
        if label:
            cv2.putText(out, "FEED: " + label[:64], (8, out.shape[0] - 60),
                        _OVERLAY_FONT, 0.45, _COL_INFO, 1, cv2.LINE_AA)

    def _bottom_bar(self, out, r, status, col):
        h = out.shape[0]
        w = out.shape[1]
        cv2.rectangle(out, (0, h - 26), (w, h), (12, 14, 16), -1)
        fps = status.get("fps", 0.0)
        cv2.putText(out, f"FPS {fps:4.1f}   ANOM-SCORE {r['anomaly_score']:5.2f}",
                    (8, h - 8), _OVERLAY_FONT, 0.45, (200, 210, 220), 1, cv2.LINE_AA)
        # score gauge
        gw, gh = 150, 10
        gx, gy = w - gw - 10, h - gh - 8
        cv2.rectangle(out, (gx, gy), (gx + gw, gy + gh), (60, 66, 72), -1)
        frac = min(1.0, r["anomaly_score"] / max(1e-6, self.model.threshold if self.model else 1))
        cv2.rectangle(out, (gx, gy), (gx + int(gw * frac), gy + gh), col, -1)
        cv2.rectangle(out, (gx, gy), (gx + gw, gy + gh), _COL_DIM, 1)

    def _rec_blink(self, out, r, w):
        if r["frame_no"] % 20 < 12 and (self.model is not None and self.model.ready):
            cv2.circle(out, (w - 22, 34), 5, (77, 77, 255), -1)
            cv2.putText(out, "REC", (w - 52, 40), _OVERLAY_FONT, 0.45, (255, 255, 255), 1)

    # ---------------- offline "NO SIGNAL" test card ----------------

    @staticmethod
    def offline_frame(cfg_pair: tuple[int, int]) -> np.ndarray:
        w, h = cfg_pair
        frame = np.zeros((h, w, 3), np.uint8)
        frame[:] = (26, 26, 26)
        for i in range(h):
            shade = 26 + int(30 * i / h)
            frame[i, :] = (shade, shade, shade)
        noise = np.random.default_rng(int(time.time() * 1000) % 2**31).integers(
            0, 28, (h, w, 1), np.uint8)
        frame = np.clip(frame.astype(np.int16) - noise.astype(np.int16), 0, 255).astype(np.uint8)
        harness = {
            "top-left": "SIGNAL LOST",
            "bottom-right": f"{w}x{h}",
        }
        cv2.putText(frame, "NO SIGNAL", (w // 2 - 130, h // 2), _OVERLAY_FONT, 1.4,
                    _COL_CRIT, 3, cv2.LINE_AA)
        cv2.putText(frame, harness["top-left"], (w // 2 - 128, h // 2 + 32),
                    _OVERLAY_FONT, 0.6, _COL_DIM, 1, cv2.LINE_AA)
        cv2.putText(frame, f"TEST CARD {harness['bottom-right']}", (w // 2 - 128, h // 2 + 56),
                    _OVERLAY_FONT, 0.5, _COL_DIM, 1, cv2.LINE_AA)
        return frame
