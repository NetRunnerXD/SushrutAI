"""Dedicated screening models (see base.py)."""
from app.vision.specialists import chest_xray  # noqa: F401  registers on import
from app.vision.specialists.base import Finding, Specialist, for_scan_type  # noqa: F401
