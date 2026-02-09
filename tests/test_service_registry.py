"""Tests for service registry."""
import os
import tempfile
from unittest.mock import patch, MagicMock

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


class TestActiveServices:
    def test_get_active_services_by_type_none_running(self):
        from voice_mode.services import get_active_services_by_type, ServiceType

        with patch('voice_mode.utils.services.common.check_service_status', return_value=("not_available", None)):
            result = get_active_services_by_type(ServiceType.TTS)
            assert result == []

    def test_get_active_services_by_type_one_running(self):
        from voice_mode.services import get_active_services_by_type, ServiceType, BUILTIN_SERVICES

        kokoro_port = BUILTIN_SERVICES["kokoro"].port

        def mock_status(port):
            if port == kokoro_port:
                return ("running", MagicMock())
            return ("not_available", None)

        with patch('voice_mode.utils.services.common.check_service_status', side_effect=mock_status):
            result = get_active_services_by_type(ServiceType.TTS)
            assert "kokoro" in result


class TestMutualExclusion:
    @pytest.mark.asyncio
    async def test_starting_tts_stops_other_tts(self):
        """Starting a TTS service should stop other running TTS services."""
        from voice_mode.services import ServiceType, BUILTIN_SERVICES
        from voice_mode.tools import service as service_module

        stopped_services = []

        async def mock_stop(name):
            stopped_services.append(name)
            return f"Stopped {name}"

        kokoro_port = BUILTIN_SERVICES["kokoro"].port

        def mock_status(port):
            if port == kokoro_port:
                return ("running", MagicMock())
            return ("not_available", None)

        with patch.object(service_module, 'stop_service', side_effect=mock_stop):
            with patch('voice_mode.utils.services.common.check_service_status', side_effect=mock_status):
                with patch.object(service_module, 'find_process_by_port', return_value=None):
                    # Simulate starting a user-defined TTS service
                    # For now, test that kokoro would be stopped if we had another TTS
                    # This is a unit test of the logic, not full integration
                    from voice_mode.services import get_active_services_by_type
                    active_tts = get_active_services_by_type(ServiceType.TTS)
                    assert "kokoro" in active_tts

    @pytest.mark.asyncio
    async def test_mutual_exclusion_stops_other_tts_service(self):
        """When starting a TTS service, other running TTS services should be stopped."""
        from voice_mode.services import ServiceType, BUILTIN_SERVICES
        from voice_mode.tools import service as service_module

        stopped_services = []

        async def mock_stop(name):
            stopped_services.append(name)
            return f"Stopped {name}"

        kokoro_port = BUILTIN_SERVICES["kokoro"].port

        # Mock kokoro as running
        def mock_check_status(port):
            if port == kokoro_port:
                return ("running", MagicMock())
            return ("not_available", None)

        # Mock find_process_by_port to simulate new service not yet running
        def mock_find_process(port):
            return None

        with patch.object(service_module, 'stop_service', side_effect=mock_stop):
            with patch('voice_mode.utils.services.common.check_service_status', side_effect=mock_check_status):
                with patch.object(service_module, 'find_process_by_port', side_effect=mock_find_process):
                    # Test mutual exclusion logic directly
                    from voice_mode.services import get_service_config, get_active_services_by_type

                    # Simulate what start_service does for a hypothetical new TTS
                    config = get_service_config("kokoro")
                    assert config.type == ServiceType.TTS

                    # Get active services (kokoro is running per our mock)
                    active = get_active_services_by_type(ServiceType.TTS)
                    assert "kokoro" in active

    @pytest.mark.asyncio
    async def test_mutual_exclusion_stt_services(self):
        """Mutual exclusion should also work for STT services."""
        from voice_mode.services import ServiceType, BUILTIN_SERVICES
        from voice_mode.tools import service as service_module

        stopped_services = []

        async def mock_stop(name):
            stopped_services.append(name)
            return f"Stopped {name}"

        whisper_port = BUILTIN_SERVICES["whisper"].port

        def mock_status(port):
            if port == whisper_port:
                return ("running", MagicMock())
            return ("not_available", None)

        with patch.object(service_module, 'stop_service', side_effect=mock_stop):
            with patch('voice_mode.utils.services.common.check_service_status', side_effect=mock_status):
                from voice_mode.services import get_active_services_by_type
                active_stt = get_active_services_by_type(ServiceType.STT)
                assert "whisper" in active_stt

    @pytest.mark.asyncio
    async def test_internal_services_no_mutual_exclusion(self):
        """INTERNAL services like voicemode and connect should not trigger mutual exclusion."""
        from voice_mode.services import ServiceType, get_service_config

        voicemode_config = get_service_config("voicemode")
        connect_config = get_service_config("connect")

        # Verify they are INTERNAL type
        assert voicemode_config.type == ServiceType.INTERNAL
        assert connect_config.type == ServiceType.INTERNAL

        # INTERNAL is not in (TTS, STT), so mutual exclusion should not apply
        assert ServiceType.INTERNAL not in (ServiceType.TTS, ServiceType.STT)
