"""
CityWatch AI - package root.

Exposes the two AI subsystems:
    detector  .. per-frame perception (motion / faces / feature encoding)
    anomaly   .. the TRAINED scene-anomaly baseline model (Gaussian).
"""

__version__ = "1.0.0"

from .anomaly import BaselineAnomalyModel  # noqa: F401
from .detector import FEATURE_NAMES, VisionAI  # noqa: F401
