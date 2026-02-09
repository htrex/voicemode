"""Service registry for VoiceMode."""

from dataclasses import dataclass
from typing import Optional

from .types import ServiceType


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
