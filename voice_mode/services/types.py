"""Service type definitions."""

from enum import Enum


class ServiceType(Enum):
    """Type of service for mutual exclusion."""
    TTS = "tts"
    STT = "stt"
    INTERNAL = "internal"
