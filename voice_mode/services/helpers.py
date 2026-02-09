"""Helper functions for service management.

These functions are called dynamically by the service registry to provide
service-specific behavior without if/elif blocks in service.py.
"""

import importlib
import os
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import ServiceConfig


def call_function_by_path(fn_path: str, *args, **kwargs) -> Any:
    """Dynamically import and call a function by its dotted path.

    Args:
        fn_path: Full path to function, e.g. "voice_mode.services.helpers.get_whisper_config_vars"
        *args, **kwargs: Arguments to pass to the function

    Returns:
        The return value of the called function
    """
    module_path, func_name = fn_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)
    return func(*args, **kwargs)


# =============================================================================
# Config vars functions - return template variables for systemd/launchd files
# =============================================================================

def get_whisper_config_vars() -> Dict[str, Any]:
    """Get config vars for whisper service template."""
    voicemode_dir = os.path.expanduser(os.environ.get("VOICEMODE_BASE_DIR", "~/.voicemode"))
    home = os.path.expanduser("~")

    whisper_dir = os.path.join(voicemode_dir, "services", "whisper")
    start_script = os.path.join(whisper_dir, "bin", "start-whisper-server.sh")

    return {
        "HOME": home,
        "START_SCRIPT": start_script,
    }


def get_kokoro_config_vars() -> Dict[str, Any]:
    """Get config vars for kokoro service template."""
    from voice_mode.utils.services.kokoro_helpers import find_kokoro_fastapi, has_gpu_support

    voicemode_dir = os.path.expanduser(os.environ.get("VOICEMODE_BASE_DIR", "~/.voicemode"))
    home = os.path.expanduser("~")

    kokoro_dir = find_kokoro_fastapi()
    if not kokoro_dir:
        kokoro_dir = os.path.join(voicemode_dir, "services", "kokoro")

    # Find start script
    start_script = None
    if platform.system() == "Darwin":
        start_script = Path(kokoro_dir) / "start-gpu_mac.sh"
    else:
        # On Linux, prefer GPU script if GPU is available, otherwise use CPU script
        if has_gpu_support():
            possible_scripts = [
                Path(kokoro_dir) / "start-gpu.sh",
                Path(kokoro_dir) / "start-cpu.sh"
            ]
        else:
            possible_scripts = [
                Path(kokoro_dir) / "start-cpu.sh",
                Path(kokoro_dir) / "start-gpu.sh"
            ]

        for script in possible_scripts:
            if script.exists():
                start_script = script
                break

    return {
        "HOME": home,
        "START_SCRIPT": str(start_script) if start_script and start_script.exists() else "",
        "KOKORO_DIR": kokoro_dir,
    }


def get_voicemode_config_vars() -> Dict[str, Any]:
    """Get config vars for voicemode service template."""
    voicemode_dir = os.path.expanduser(os.environ.get("VOICEMODE_BASE_DIR", "~/.voicemode"))
    home = os.path.expanduser("~")

    start_script = os.path.join(voicemode_dir, "services", "voicemode", "bin", "start-voicemode-serve.sh")

    return {
        "HOME": home,
        "START_SCRIPT": start_script,
    }


def get_connect_config_vars() -> Dict[str, Any]:
    """Get config vars for connect service template."""
    voicemode_dir = os.path.expanduser(os.environ.get("VOICEMODE_BASE_DIR", "~/.voicemode"))
    home = os.path.expanduser("~")

    start_script = os.path.join(voicemode_dir, "services", "connect", "bin", "start-voicemode-connect.sh")

    return {
        "HOME": home,
        "START_SCRIPT": start_script,
    }


def get_generic_config_vars(config: "ServiceConfig") -> Dict[str, Any]:
    """Get config vars for user-defined services.

    Args:
        config: ServiceConfig from registry

    Returns:
        Dict with HOME, START_SCRIPT, and SERVICE_NAME
    """
    home = os.path.expanduser("~")
    start_script = os.path.expanduser(config.start_script) if config.start_script else ""

    return {
        "HOME": home,
        "START_SCRIPT": start_script,
        "SERVICE_NAME": config.template_name,
    }


# =============================================================================
# Status info functions - return extra status info for running services
# =============================================================================

def get_whisper_status_info(proc: Any, cmdline: List[str]) -> List[str]:
    """Get whisper-specific status info.

    Args:
        proc: psutil.Process object
        cmdline: Process command line as list

    Returns:
        List of status info strings
    """
    parts = []

    # Get model info
    model = "unknown"
    model_name = None
    for i, arg in enumerate(cmdline):
        if arg == "--model" and i + 1 < len(cmdline):
            model = Path(cmdline[i + 1]).name
            # Extract model name from filename (e.g., ggml-large-v3-turbo.bin -> large-v3-turbo)
            if model.startswith("ggml-") and model.endswith(".bin"):
                model_name = model[5:-4]
            break
    parts.append(f"Model: {model}")

    # Get version and capability info
    try:
        from voice_mode.utils.services.whisper_version import get_whisper_version_info, check_coreml_model_exists
        version_info = get_whisper_version_info()

        if version_info.get("version"):
            parts.append(f"Version: {version_info['version']}")
        elif version_info.get("commit"):
            parts.append(f"Commit: {version_info['commit']}")

        # Show Core ML status on Apple Silicon
        if platform.machine() == "arm64" and platform.system() == "Darwin":
            if version_info.get("coreml_supported"):
                # Check if the current model has Core ML
                if model_name and check_coreml_model_exists(model_name):
                    parts.append("Core ML: Enabled & Active")
                else:
                    parts.append("Core ML: Supported (model not converted)")
            else:
                parts.append("Core ML: Not compiled in")

        # Show GPU support
        gpu_support = []
        if version_info.get("metal_supported"):
            gpu_support.append("Metal")
        if version_info.get("cuda_supported"):
            gpu_support.append("CUDA")
        if gpu_support:
            parts.append(f"GPU: {', '.join(gpu_support)}")
    except Exception:
        pass

    return parts


def get_kokoro_status_info(proc: Any, cmdline: List[str]) -> List[str]:
    """Get kokoro-specific status info.

    Args:
        proc: psutil.Process object
        cmdline: Process command line as list

    Returns:
        List of status info strings
    """
    parts = []

    # Try to get version info
    try:
        from voice_mode.utils.services.version_info import get_kokoro_version
        version_info = get_kokoro_version()
        if version_info.get("api_version"):
            parts.append(f"API Version: {version_info['api_version']}")
        elif version_info.get("version"):
            parts.append(f"Version: {version_info['version']}")
    except Exception:
        pass

    return parts


def get_voicemode_status_info(proc: Any, cmdline: List[str]) -> List[str]:
    """Get voicemode-specific status info.

    Args:
        proc: psutil.Process object
        cmdline: Process command line as list

    Returns:
        List of status info strings
    """
    parts = []

    # Extract transport and host from command line
    transport = "streamable-http"  # default
    host = "127.0.0.1"  # default
    for i, arg in enumerate(cmdline):
        if arg == "--transport" and i + 1 < len(cmdline):
            transport = cmdline[i + 1]
        elif arg == "--host" and i + 1 < len(cmdline):
            host = cmdline[i + 1]
    parts.append(f"Transport: {transport}")
    parts.append(f"Host: {host}")

    # Try to get version info
    try:
        from voice_mode.version import __version__
        parts.append(f"Version: {__version__}")
    except Exception:
        pass

    return parts


def get_connect_status_info(proc: Any, cmdline: List[str]) -> List[str]:
    """Get connect-specific status info.

    Args:
        proc: psutil.Process object
        cmdline: Process command line as list

    Returns:
        List of status info strings
    """
    parts = []

    parts.append("Mode: standby")
    parts.append("Type: WebSocket client")

    # Try to get version info
    try:
        from voice_mode.version import __version__
        parts.append(f"Version: {__version__}")
    except Exception:
        pass

    return parts


# =============================================================================
# Pre-enable validation functions
# =============================================================================

def validate_whisper_pre_enable(config_vars: Dict[str, Any]) -> Optional[str]:
    """Validate prerequisites before enabling whisper service.

    Args:
        config_vars: Config variables from get_service_config_vars()

    Returns:
        Error message if validation fails, None if OK
    """
    start_script = config_vars.get("START_SCRIPT", "")
    if not start_script or not Path(start_script).exists():
        return "Whisper start script not found. Please run whisper_install first."
    return None


def validate_kokoro_pre_enable(config_vars: Dict[str, Any]) -> Optional[str]:
    """Validate prerequisites before enabling kokoro service.

    Args:
        config_vars: Config variables from get_service_config_vars()

    Returns:
        Error message if validation fails, None if OK
    """
    start_script = config_vars.get("START_SCRIPT", "")
    if not start_script or not Path(start_script).exists():
        return "Kokoro start script not found. Please run kokoro_install first."
    return None


def validate_connect_pre_enable(config_vars: Dict[str, Any]) -> Optional[str]:
    """Validate prerequisites before enabling connect service.

    Args:
        config_vars: Config variables from get_service_config_vars()

    Returns:
        Error message if validation fails, None if OK
    """
    # Validate OAuth credentials exist before enabling connect service
    credentials_file = Path.home() / ".voicemode" / "credentials"
    if not credentials_file.exists():
        return "No credentials found. Run 'voicemode connect login' first to authenticate."
    return None


def validate_voicemode_pre_enable(config_vars: Dict[str, Any]) -> Optional[str]:
    """Validate prerequisites before enabling voicemode service.

    Args:
        config_vars: Config variables from get_service_config_vars()

    Returns:
        Error message if validation fails, None if OK
        Note: voicemode can auto-install, so this just checks; auto_install handles the fix
    """
    start_script = config_vars.get("START_SCRIPT", "")
    if not start_script or not Path(start_script).exists():
        return "VoiceMode start script not found."
    return None


def validate_generic_pre_enable(config_vars: Dict[str, Any]) -> Optional[str]:
    """Validate prerequisites before enabling a generic user service.

    Args:
        config_vars: Config variables from get_service_config_vars()

    Returns:
        Error message if validation fails, None if OK
    """
    start_script = config_vars.get("START_SCRIPT", "")
    if not start_script:
        return "No start_script configured for this service."
    if not Path(start_script).exists():
        return f"Start script not found: {start_script}"
    return None


# =============================================================================
# Fallback start command functions - return (cmd, cwd, error) for direct start
# =============================================================================

def get_whisper_start_cmd(config: "ServiceConfig") -> tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """Get command to start whisper service directly (fallback when no service manager).

    Args:
        config: ServiceConfig from registry

    Returns:
        Tuple of (cmd, cwd, error). If error is not None, cmd/cwd are None.
    """
    from voice_mode.utils.services.whisper_helpers import find_whisper_server, find_whisper_model

    whisper_bin = find_whisper_server()
    if not whisper_bin:
        return None, None, "whisper-server not found. Please run whisper_install first."

    model_file = find_whisper_model()
    if not model_file:
        return None, None, "No Whisper model found. Please run download_model first."

    cmd = [str(whisper_bin), "--host", "0.0.0.0", "--port", str(config.port), "--model", str(model_file)]
    return cmd, None, None


def get_kokoro_start_cmd(config: "ServiceConfig") -> tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """Get command to start kokoro service directly (fallback when no service manager).

    Args:
        config: ServiceConfig from registry

    Returns:
        Tuple of (cmd, cwd, error). If error is not None, cmd/cwd are None.
    """
    from voice_mode.utils.services.kokoro_helpers import find_kokoro_fastapi, has_gpu_support

    kokoro_dir = find_kokoro_fastapi()
    if not kokoro_dir:
        return None, None, "kokoro-fastapi not found. Please run kokoro_install first."

    # Use appropriate start script
    if platform.system() == "Darwin":
        start_script = Path(kokoro_dir) / "start-gpu_mac.sh"
    else:
        if has_gpu_support():
            possible_scripts = [
                Path(kokoro_dir) / "start-gpu.sh",
                Path(kokoro_dir) / "start-cpu.sh"
            ]
        else:
            possible_scripts = [
                Path(kokoro_dir) / "start-cpu.sh",
                Path(kokoro_dir) / "start-gpu.sh"
            ]

        start_script = None
        for script in possible_scripts:
            if script.exists():
                start_script = script
                break

    if not start_script or not start_script.exists():
        return None, None, f"Start script not found: {start_script}"

    cmd = [str(start_script)]
    return cmd, kokoro_dir, None


def get_voicemode_start_cmd(config: "ServiceConfig") -> tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """Get command to start voicemode service directly (fallback when no service manager).

    Args:
        config: ServiceConfig from registry

    Returns:
        Tuple of (cmd, cwd, error). If error is not None, cmd/cwd are None.
    """
    import sys
    cmd = [sys.executable, "-m", "voice_mode", "serve", "--port", str(config.port)]
    return cmd, None, None


def get_connect_start_cmd(config: "ServiceConfig") -> tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """Get command to start connect service directly (fallback when no service manager).

    Args:
        config: ServiceConfig from registry

    Returns:
        Tuple of (cmd, cwd, error). If error is not None, cmd/cwd are None.
    """
    import sys
    cmd = [sys.executable, "-m", "voice_mode", "connect", "standby"]
    return cmd, None, None


def get_generic_start_cmd(config: "ServiceConfig") -> tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """Get command to start a generic user service directly (fallback when no service manager).

    Args:
        config: ServiceConfig from registry

    Returns:
        Tuple of (cmd, cwd, error). If error is not None, cmd/cwd are None.
    """
    if not config.start_script:
        return None, None, f"No start script configured for {config.template_name}"

    start_script = os.path.expanduser(config.start_script)
    if not Path(start_script).exists():
        return None, None, f"Start script not found: {start_script}"

    cmd = [start_script]
    return cmd, None, None
