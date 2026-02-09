"""Tests for service registry."""
import os
import tempfile

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


class TestBuiltinServices:
    def test_builtin_services_exist(self):
        from voice_mode.services import BUILTIN_SERVICES

        assert "whisper" in BUILTIN_SERVICES
        assert "kokoro" in BUILTIN_SERVICES
        assert "voicemode" in BUILTIN_SERVICES
        assert "connect" in BUILTIN_SERVICES

    def test_whisper_config(self):
        from voice_mode.services import BUILTIN_SERVICES, ServiceType

        whisper = BUILTIN_SERVICES["whisper"]
        assert whisper.type == ServiceType.STT
        assert whisper.port == 2022  # Default WHISPER_PORT
        assert whisper.template_name == "whisper"

    def test_kokoro_config(self):
        from voice_mode.services import BUILTIN_SERVICES, ServiceType

        kokoro = BUILTIN_SERVICES["kokoro"]
        assert kokoro.type == ServiceType.TTS
        assert kokoro.template_name == "kokoro"

    def test_service_types_correct(self):
        from voice_mode.services import BUILTIN_SERVICES, ServiceType

        assert BUILTIN_SERVICES["whisper"].type == ServiceType.STT
        assert BUILTIN_SERVICES["kokoro"].type == ServiceType.TTS
        assert BUILTIN_SERVICES["voicemode"].type == ServiceType.INTERNAL
        assert BUILTIN_SERVICES["connect"].type == ServiceType.INTERNAL


class TestUserServices:
    def test_load_user_services_no_file(self):
        from voice_mode.services.registry import load_user_services

        # With non-existent path, should return empty dict
        result = load_user_services("/nonexistent/path/services.yaml")
        assert result == {}

    def test_load_user_services_valid_yaml(self):
        from voice_mode.services.registry import load_user_services
        from voice_mode.services import ServiceType

        yaml_content = """
services:
  chatterbox:
    type: tts
    port: 8881
    template: generic

  azzurra:
    type: tts
    port: 8890
    template: generic
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            f.flush()

            try:
                result = load_user_services(f.name)

                assert "chatterbox" in result
                assert "azzurra" in result
                assert result["chatterbox"].type == ServiceType.TTS
                assert result["chatterbox"].port == 8881
                assert result["azzurra"].port == 8890
            finally:
                os.unlink(f.name)

    def test_get_service_registry_combines_builtin_and_user(self):
        from voice_mode.services.registry import get_service_registry

        # Without user services, should have builtin only
        registry = get_service_registry()
        assert "whisper" in registry
        assert "kokoro" in registry
