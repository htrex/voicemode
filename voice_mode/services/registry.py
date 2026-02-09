"""Service registry for VoiceMode."""

from dataclasses import dataclass
from typing import Optional

from .types import ServiceType
from voice_mode.config import WHISPER_PORT, KOKORO_PORT


@dataclass
class ServiceConfig:
    """Configuration for a managed service."""
    type: ServiceType
    port: int
    template_name: str
    start_script: Optional[str] = None
    stop_script: Optional[str] = None
    log_name: Optional[str] = None
    health_check_url: Optional[str] = None
    status_info_fn: Optional[str] = None
    pre_start_fn: Optional[str] = None


# Default port for VoiceMode serve
VOICEMODE_SERVE_PORT = 8765

BUILTIN_SERVICES: dict[str, ServiceConfig] = {
    "whisper": ServiceConfig(
        type=ServiceType.STT,
        port=WHISPER_PORT,
        template_name="whisper",
        status_info_fn="voice_mode.utils.services.whisper_helpers.get_status_info",
    ),
    "kokoro": ServiceConfig(
        type=ServiceType.TTS,
        port=KOKORO_PORT,
        template_name="kokoro",
        status_info_fn="voice_mode.utils.services.kokoro_helpers.get_status_info",
        pre_start_fn="voice_mode.utils.services.kokoro_helpers.check_gpu",
    ),
    "voicemode": ServiceConfig(
        type=ServiceType.INTERNAL,
        port=VOICEMODE_SERVE_PORT,
        template_name="serve",
    ),
    "connect": ServiceConfig(
        type=ServiceType.INTERNAL,
        port=0,  # WebSocket client, no port
        template_name="connect",
    ),
}
