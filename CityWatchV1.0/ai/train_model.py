#!/usr/bin/env python
"""
CityWatch ACADEMY - trainable scene-anomaly model.

Trains the CityWatch baseline AI ("the trained AI") on the live camera:

    python -m ai.train_model --seconds 45              # train on live camera
    python -m ai.train_model --seconds 90 --sector X   # name the session
    python -m ai.train_model --demo                    # deterministic synthetic session
    python -m ai.train_model --labeled data/normal.csv # from an exported CSV

The resulting model is saved to  models/scene_baseline.npz  and is loaded
automatically by server.py on every start. Runtime retraining is also
possible from the dashboard ("RETRAIN MODEL" button).
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from .anomaly import BaselineAnomalyModel
from .detector import FEATURE_NAMES, VisionAI

W, H = 640, 480


def synthetic_session(n: int, seed: int, drift: float = 0.8) -> np.ndarray:
    """Deterministic 'quiet city sector' training data.

    Simulates the joint distribution of (faces, motion, brightness, edge)
    for a normal surveillance scene so the model can be created and graded
    without camera hardware present.
    """
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 4 * np.pi, n)
    faces = np.clip(rng.poisson(1.6, n).astype(float), 0, 5)
    motion = 0.055 + 0.025 * rng.standard_cauchy(n)
    motion = np.clip(motion, 0.002, drift * 0.4)
    brightness = 0.46 + 0.05 * np.sin(t / 3) + 0.02 * rng.standard_normal(n)
    edge = 0.16 + 0.03 * np.sin(t / 2) + 0.015 * rng.standard_normal(n)
    X = np.stack([faces, motion, brightness, edge], axis=1)
    X = np.clip(X, [0, 0.0005, 0.02, 0.01], [6, 1.0, 1.0, 1.0])
    return X


def live_session(seconds: float, camera: int | str, cfg: dict) -> np.ndarray:
    """Collect real features straight from the live camera."""
    import cv2

    cap = cv2.VideoCapture(0 if isinstance(camera, int) else camera)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["frame_width"])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["frame_height"])
    if not cap.isOpened():
        cap.release()
        raise RuntimeError("camera unavailable - use --demo instead")
    ai = VisionAI(cfg, camera_id="TRAIN-CAM", sector="TRAIN-SECTOR", model=None)
    rows: list[list[float]] = []
    t0 = time.time()
    while len(rows) < 320 and time.time() - t0 < seconds:
        ok, frame = cap.read()
        if not ok:
            continue
        feats = ai.analyze(frame)["features"]
        rows.append([feats[k] for k in FEATURE_NAMES])
        print(f"  collecting {len(rows):3d}/320  ({time.time() - t0:4.1f}s)", end="\r")
    cap.release()
    print()
    if len(rows) < 30:
        raise RuntimeError("too few usable frames - improve lighting / duration")
    return np.array(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="CityWatch AI model trainer")
    ap.add_argument("--seconds", type=float, default=45.0,
                    help="calibration capture duration for --live")
    ap.add_argument("--live", action="store_true", help="capture from webcam/RTSP")
    ap.add_argument("--demo", action="store_true", help="deterministic synthetic session")
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--mode", default="live", help="session label stored in the model")
    args = ap.parse_args()

    model = BaselineAnomalyModel()
    if args.live:
        cfg = {"frame_width": W, "frame_height": H, "detection": {}}
        X = live_session(args.seconds, 0, cfg)
        mode = "live-webcast"
    elif args.demo:
        X = synthetic_session(420, args.seed)
        mode = "demo-synthetic"
    else:
        ap.error("choose --live or --demo")

    print(f"[CVW-AI] fitting Gaussian baseline on {X.shape[0]} samples ...")
    report = model.fit(X, mode=mode)
    model.threshold = round(model.threshold, 3)
    model.save()
    print("[CVW-AI] trained OK  ->  models/scene_baseline.npz")
    print(f"  samples   : {report['samples']}")
    print(f"  threshold : {report['threshold']}  (Mahalanobis)")
    print(f"  mean      : {report['mean']}")
    print(f"  sensors   : {list(zip(report['sensors'], report['features']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
