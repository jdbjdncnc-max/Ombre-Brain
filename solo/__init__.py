"""Solitude system runtime services and emotion primitives."""

from .emotion_model import CHANNELS, BUCKETS
from .service import SoloService

__all__ = ["BUCKETS", "CHANNELS", "SoloService"]
