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


class TestServiceConfig:
    def test_service_config_creation(self):
        from voice_mode.services import ServiceConfig, ServiceType

        config = ServiceConfig(
            type=ServiceType.TTS,
            port=8880,
            template_name="kokoro",
        )

        assert config.type == ServiceType.TTS
        assert config.port == 8880
        assert config.template_name == "kokoro"
        assert config.start_script is None
        assert config.log_name is None

    def test_service_config_with_optional_fields(self):
        from voice_mode.services import ServiceConfig, ServiceType

        config = ServiceConfig(
            type=ServiceType.STT,
            port=2022,
            template_name="whisper",
            start_script="/path/to/start.sh",
            log_name="whisper",
            health_check_url="http://localhost:2022/health",
        )

        assert config.start_script == "/path/to/start.sh"
        assert config.log_name == "whisper"
        assert config.health_check_url == "http://localhost:2022/health"
