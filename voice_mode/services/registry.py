"""Service registry for VoiceMode."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

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
    config_vars_fn: Optional[str] = None  # Function path for getting template config vars
    pre_enable_fn: Optional[str] = None   # Function path for pre-enable validation
    auto_install_fn: Optional[str] = None # Function path for auto-install start script (async)
    start_cmd_fn: Optional[str] = None    # Function path for getting fallback start command
    process_name: Optional[str] = None    # Process name for macOS log filtering


# Default port for VoiceMode serve
VOICEMODE_SERVE_PORT = 8765

BUILTIN_SERVICES: dict[str, ServiceConfig] = {
    "whisper": ServiceConfig(
        type=ServiceType.STT,
        port=WHISPER_PORT,
        template_name="whisper",
        config_vars_fn="voice_mode.services.helpers.get_whisper_config_vars",
        status_info_fn="voice_mode.services.helpers.get_whisper_status_info",
        pre_enable_fn="voice_mode.services.helpers.validate_whisper_pre_enable",
        start_cmd_fn="voice_mode.services.helpers.get_whisper_start_cmd",
        process_name="whisper-server",
    ),
    "kokoro": ServiceConfig(
        type=ServiceType.TTS,
        port=KOKORO_PORT,
        template_name="kokoro",
        config_vars_fn="voice_mode.services.helpers.get_kokoro_config_vars",
        status_info_fn="voice_mode.services.helpers.get_kokoro_status_info",
        pre_enable_fn="voice_mode.services.helpers.validate_kokoro_pre_enable",
        start_cmd_fn="voice_mode.services.helpers.get_kokoro_start_cmd",
        pre_start_fn="voice_mode.utils.services.kokoro_helpers.check_gpu",
        process_name="kokoro-fastapi",
    ),
    "voicemode": ServiceConfig(
        type=ServiceType.INTERNAL,
        port=VOICEMODE_SERVE_PORT,
        template_name="serve",
        config_vars_fn="voice_mode.services.helpers.get_voicemode_config_vars",
        status_info_fn="voice_mode.services.helpers.get_voicemode_status_info",
        pre_enable_fn="voice_mode.services.helpers.validate_voicemode_pre_enable",
        auto_install_fn="voice_mode.tools.service.install_voicemode_start_script",
        start_cmd_fn="voice_mode.services.helpers.get_voicemode_start_cmd",
        process_name="voicemode-serve",
    ),
    "connect": ServiceConfig(
        type=ServiceType.INTERNAL,
        port=0,  # WebSocket client, no port
        template_name="connect",
        config_vars_fn="voice_mode.services.helpers.get_connect_config_vars",
        status_info_fn="voice_mode.services.helpers.get_connect_status_info",
        pre_enable_fn="voice_mode.services.helpers.validate_connect_pre_enable",
        auto_install_fn="voice_mode.tools.service.install_voicemode_connect_script",
        start_cmd_fn="voice_mode.services.helpers.get_connect_start_cmd",
        process_name="voicemode",
    ),
}


def load_user_services(config_path: str = None) -> dict[str, ServiceConfig]:
    """Load user-defined services from YAML config.

    Args:
        config_path: Path to services.yaml. Defaults to ~/.voicemode/services.yaml

    Returns:
        Dict of service name to ServiceConfig
    """
    if config_path is None:
        config_path = Path("~/.voicemode/services.yaml").expanduser()
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        return {}

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if not data or "services" not in data:
        return {}

    services = {}
    for name, cfg in data["services"].items():
        services[name] = ServiceConfig(
            type=ServiceType(cfg["type"]),
            port=cfg["port"],
            template_name=cfg.get("template", "generic"),
            start_script=cfg.get("start_script"),
            stop_script=cfg.get("stop_script"),
            log_name=cfg.get("log_name"),
            health_check_url=cfg.get("health_check"),
            status_info_fn=cfg.get("status_info_fn"),
            pre_start_fn=cfg.get("pre_start_fn"),
            config_vars_fn=cfg.get("config_vars_fn"),
            pre_enable_fn=cfg.get("pre_enable_fn"),
            auto_install_fn=cfg.get("auto_install_fn"),
            start_cmd_fn=cfg.get("start_cmd_fn"),
            process_name=cfg.get("process_name"),
        )
    return services


def get_service_registry() -> dict[str, ServiceConfig]:
    """Get combined registry of builtin and user services.

    User services can override builtin services with same name.
    """
    return {**BUILTIN_SERVICES, **load_user_services()}


def get_service_config(service_name: str) -> ServiceConfig:
    """Get config for a service by name.

    Raises:
        ValueError: If service not found in registry
    """
    registry = get_service_registry()
    if service_name not in registry:
        available = ", ".join(sorted(registry.keys()))
        raise ValueError(f"Unknown service: {service_name}. Available: {available}")
    return registry[service_name]


def get_active_services_by_type(service_type: ServiceType) -> list[str]:
    """Find all running services of a given type.

    Args:
        service_type: Type of service (TTS, STT, INTERNAL)

    Returns:
        List of service names that are currently running
    """
    from voice_mode.utils.services.common import check_service_status

    active = []
    registry = get_service_registry()

    for name, config in registry.items():
        if config.type != service_type:
            continue
        if config.port == 0:  # Services like 'connect' don't use ports
            continue

        status, _ = check_service_status(config.port)
        if status == "running":
            active.append(name)

    return active
