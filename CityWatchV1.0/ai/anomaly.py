"""
CityWatch Trained AI - Adaptive Scene Anomaly Model.

This is our own trained machine-learning model ("the trained AI").

WHAT IT LEARNS
    Every camera frame is encoded into a 4-D feature vector:
        0. face_count     - people detected in frame
        1. motion_ratio   - fraction of frame in motion
        2. brightness     - mean scene luminance
        3. edge_density   - structural activity (texture / clutter)

    The model LEARNS the normal statistical signature of the watched scene
    from a supervised capture session (~10 s to ~10 min of live feed), then
    models "normal" as a multivariate Gaussian:  N(mu, Sigma).

HOW IT SCORES
    At runtime, each frame's Mahalanobis distance to mu (with covariance
    shrinkage for numerical stability) becomes an anomaly score. Distance
    above the alert threshold (learned from training data: the highest
    observed normal score plus 15% headroom) raises a CRITICAL alert.

Trained artifacts are stored in models/scene_baseline.npz
Run ai/train_model.py to (re)train against the live camera.
"""

from __future__ import annotations

import json
import os
import time

import numpy as np

from .detector import FEATURE_NAMES

_MODEL_PATH = None  # placeholder keeps flake imports tidy
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "scene_baseline.npz")
SHRINK = 0.25  # covariance shrinkage -> stable inverse on short sessions

# Per-feature minimum plausible sigma. Features that never varied during a
# short training capture (e.g. face_count stuck at 0 -> variance exactly 0)
# previously produced enormous Mahalanobis distances (~195 sigma) the moment
# they changed. These floors keep scores meaningful and thresholdable.
_MIN_SIGMA = {
    "face_count": 1.0,     # integer counts: +/-1 is never an "anomaly"
    "motion_ratio": 0.02,  # 2% of frame in motion is normal scene jitter
    "brightness": 0.03,    # ~8 luminance levels of 255
    "edge_density": 0.03,
}

_SENSORS = ["P1", "M1", "L1", "E1"]


class BaselineAnomalyModel:
    """Multivariate Gaussian anomaly model with persistence + retraining."""

    def __init__(self, path: str = MODEL_PATH):
        self.path = path
        self.ready = False
        self.threshold = 1.0
        self.mean = np.zeros(len(FEATURE_NAMES))
        self.cov = np.eye(len(FEATURE_NAMES))
        self.precision = self.cov
        self._inv = np.linalg.inv(self.cov)
        self.n_samples = 0
        self.trained_at = None
        self.mode = "untrained"
        self.load()

    # ---------------- persistence ----------------

    def load(self) -> bool:
        feats = FEATURE_NAMES
        try:
            with np.load(self.path, allow_pickle=False) as z:
                self.mean = z["mean"].astype(float)
                self.cov = z["cov"].astype(float)
                self.threshold = float(z["threshold"])
                self.n_samples = int(z["n_samples"])
                self.trained_at = str(z["trained_at"]) if "trained_at" in z else None
                self.mode = str(z["mode"]) if "mode" in z else "legacy"
                if "features" in z:
                    feats = json.loads(str(z["features"]))
        except Exception:
            return False
        if feats != FEATURE_NAMES:
            return False
        self._finalise()
        return self.ready

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.trained_at = time.strftime("%Y-%m-%d %H:%M:%S")
        np.savez(
            self.path,
            mean=self.mean,
            cov=self.cov,
            threshold=np.asarray(self.threshold),
            n_samples=self.n_samples,
            features=json.dumps(FEATURE_NAMES),
            trained_at=self.trained_at,
            mode=self.mode,
        )

    def _finalise(self) -> None:
        d = self.mean.shape[0]
        cov = self.cov.copy()
        # Floor each diagonal variance so near-deterministic features cannot
        # blow the Mahalanobis distance up to meaningless hundreds of sigma.
        for i, name in enumerate(FEATURE_NAMES):
            floor = (_MIN_SIGMA.get(name, 0.02)) ** 2
            cov[i, i] = max(float(cov[i, i]), floor)
        reg = np.eye(d) * (SHRINK * max(1e-8, float(np.trace(cov)) / d))
        cov = (1.0 - SHRINK) * cov + reg
        self._inv = np.linalg.inv(cov)
        self.cov = cov
        self.precision = self._inv
        self.threshold = max(1.0, float(self.threshold))
        self.mean = self.mean.reshape(d)
        self.ready = bool(self.n_samples >= 30 and np.all(np.isfinite(self.cov)))

    # ---------------- training ----------------

    def fit(self, X: np.ndarray, mode: str = "live") -> dict:
        X = np.asarray(X, dtype=float)
        if X.ndim != 2 or X.shape[0] < 10:
            raise ValueError(f"need >=10 training samples, got {X.shape}")
        self.mean = X.mean(axis=0)
        centered = X - self.mean
        self.cov = (centered.T @ centered) / max(1, X.shape[0] - 1)
        self.n_samples = int(X.shape[0])
        self.mode = mode
        self._finalise()
        # threshold: worst normal score observed +15% headroom, with a floor
        # so motionless rooms cannot overfit to an ultra-tight baseline
        scores = np.array([self._maha(row - self.mean) for row in X])
        self.threshold = round(max(1.2, float(np.max(scores)) * 1.15 + 0.25), 3)
        self.save()
        return self.report()

    def _maha(self, delta: np.ndarray) -> float:
        return float(np.sqrt(max(0.0, delta @ self._inv @ delta)))

    # ---------------- inference ----------------

    def score(self, features: list[float]) -> tuple[float, bool]:
        if not self.ready:
            return 0.0, False
        diff = np.asarray(features, dtype=float) - self.mean
        d = self._maha(diff)
        return d, (d > self.threshold)

    # ---------------- reporting ----------------

    def report(self) -> dict:
        return {
            "ready": self.ready,
            "samples": self.n_samples,
            "trained_at": self.trained_at,
            "mode": self.mode,
            "threshold": round(float(self.threshold), 3),
            "mean": [round(float(m), 4) for m in self.mean],
            "features": FEATURE_NAMES,
            "sensors": _SENSORS,
        }
