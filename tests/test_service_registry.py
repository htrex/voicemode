"""Tests for service registry."""
import pytest


class TestServiceType:
    def test_service_type_values(self):
        from voice_mode.services import ServiceType

        assert ServiceType.TTS.value == "tts"
        assert ServiceType.STT.value == "stt"
        assert ServiceType.INTERNAL.value == "internal"

    def test_service_type_from_string(self):
        from voice_mode.services import ServiceType

        assert ServiceType("tts") == ServiceType.TTS
        assert ServiceType("stt") == ServiceType.STT
