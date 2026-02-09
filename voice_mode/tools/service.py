"""Unified service management tool for voice mode services."""

import asyncio
import logging
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Literal, Optional, Dict, Any, Union

import psutil

from voice_mode.server import mcp
from voice_mode.services import get_service_config, ServiceType, get_active_services_by_type
from voice_mode.utils.services.common import find_process_by_port, check_service_status
from voice_mode.utils.services.whisper_helpers import find_whisper_server, find_whisper_model
from voice_mode.utils.services.kokoro_helpers import find_kokoro_fastapi, has_gpu_support, is_kokoro_starting_up

logger = logging.getLogger("voicemode")


def get_service_config_vars(service_name: str) -> Dict[str, Any]:
    """Get configuration variables for service templates.

    Returns minimal vars needed by simplified templates:
    - HOME: For paths that need absolute paths (macOS plist only)
    - START_SCRIPT: Path to the service start script
    - Service-specific binaries/dirs as needed

    Config like ports/models is now handled by start scripts via voicemode.env

    Uses the service registry to dynamically call the appropriate config_vars_fn,
    enabling user-defined services without code changes.
    """
    from voice_mode.services.helpers import call_function_by_path, get_generic_config_vars

    config = get_service_config(service_name)

    if config.config_vars_fn:
        # Dynamically call the config vars function
        return call_function_by_path(config.config_vars_fn)
    else:
        # Fall back to generic config vars for user-defined services
        return get_generic_config_vars(config)


async def install_voicemode_start_script() -> Dict[str, Any]:
    """Install the VoiceMode start script to the expected location.

    Unlike whisper/kokoro which require compilation, voicemode is built into
    the package. This function copies the start script template to the
    ~/.voicemode/services/voicemode/bin/ directory.

    Returns:
        Dict with success status and start_script path
    """
    voicemode_dir = os.path.expanduser(os.environ.get("VOICEMODE_BASE_DIR", "~/.voicemode"))
    bin_dir = os.path.join(voicemode_dir, "services", "voicemode", "bin")
    start_script_path = os.path.join(bin_dir, "start-voicemode-serve.sh")

    try:
        # Create bin directory if it doesn't exist
        os.makedirs(bin_dir, exist_ok=True)

        # Load template script from package
        template_path = Path(__file__).parent.parent / "templates" / "scripts" / "start-voicemode-serve.sh"
        if not template_path.exists():
            return {"success": False, "error": f"Template not found: {template_path}"}

        template_content = template_path.read_text()

        # Write the start script
        with open(start_script_path, 'w') as f:
            f.write(template_content)

        # Make executable
        os.chmod(start_script_path, 0o755)

        logger.info(f"Installed VoiceMode start script at {start_script_path}")
        return {"success": True, "start_script": start_script_path}

    except Exception as e:
        logger.error(f"Error installing VoiceMode start script: {e}")
        return {"success": False, "error": str(e)}


async def install_voicemode_connect_script() -> Dict[str, Any]:
    """Install the VoiceMode Connect start script to the expected location.

    Similar to install_voicemode_start_script() but for the connect service.
    This function copies the start script template to the
    ~/.voicemode/services/connect/bin/ directory.

    Returns:
        Dict with success status and start_script path
    """
    voicemode_dir = os.path.expanduser(os.environ.get("VOICEMODE_BASE_DIR", "~/.voicemode"))
    bin_dir = os.path.join(voicemode_dir, "services", "connect", "bin")
    start_script_path = os.path.join(bin_dir, "start-voicemode-connect.sh")

    try:
        # Create bin directory if it doesn't exist
        os.makedirs(bin_dir, exist_ok=True)

        # Load template script from package
        template_path = Path(__file__).parent.parent / "templates" / "scripts" / "start-voicemode-connect.sh"
        if not template_path.exists():
            return {"success": False, "error": f"Template not found: {template_path}"}

        template_content = template_path.read_text()

        # Write the start script
        with open(start_script_path, 'w') as f:
            f.write(template_content)

        # Make executable
        os.chmod(start_script_path, 0o755)

        logger.info(f"Installed VoiceMode Connect start script at {start_script_path}")
        return {"success": True, "start_script": start_script_path}

    except Exception as e:
        logger.error(f"Error installing VoiceMode Connect start script: {e}")
        return {"success": False, "error": str(e)}


def find_connect_process():
    """Find the voicemode connect standby process by command line.

    Unlike other services that listen on ports, connect is a WebSocket client.
    We find it by looking for the voicemode connect standby command.

    Returns:
        psutil.Process if found, None otherwise
    """
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            # Join cmdline to search for the pattern
            cmdline_str = ' '.join(cmdline).lower()
            if 'voicemode' in cmdline_str and 'connect' in cmdline_str and 'standby' in cmdline_str:
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def load_service_template(service_name: str) -> str:
    """Load service file template from templates."""
    system = platform.system()
    templates_dir = Path(__file__).parent.parent / "templates"

    config = get_service_config(service_name)
    template_name = config.template_name

    if system == "Darwin":
        template_path = templates_dir / "launchd" / f"com.voicemode.{template_name}.plist"
    else:
        template_path = templates_dir / "systemd" / f"voicemode-{template_name}.service"

    if not template_path.exists():
        raise FileNotFoundError(f"Service template not found: {template_path}")

    return template_path.read_text()


def create_service_file(service_name: str) -> tuple[Path, str]:
    """Create service file content from template with config vars.

    This is the single source of truth for generating service files.
    Templates are simplified - start scripts handle config via voicemode.env.

    Args:
        service_name: Name of the service (whisper, kokoro, voicemode)

    Returns:
        Tuple of (destination_path, file_content)
    """
    system = platform.system()
    home = os.path.expanduser("~")

    # Load template
    template = load_service_template(service_name)

    # Get config variables
    config_vars = get_service_config_vars(service_name)

    # Format template with config vars
    content = template.format(**config_vars)

    # Get file name from registry (template_name is used for file naming)
    svc_config = get_service_config(service_name)
    file_name = svc_config.template_name

    # Determine destination path
    if system == "Darwin":
        dest_path = Path(home) / "Library" / "LaunchAgents" / f"com.voicemode.{file_name}.plist"
    else:
        dest_path = Path(home) / ".config" / "systemd" / "user" / f"voicemode-{file_name}.service"

    # Ensure log directory exists (voicemode uses 'serve' for logs, connect uses 'connect')
    log_dir = Path(home) / ".voicemode" / "logs" / file_name
    log_dir.mkdir(parents=True, exist_ok=True)

    return dest_path, content


async def status_service(service_name: str) -> str:
    """Get status of a service."""
    config = get_service_config(service_name)
    port = config.port

    # Connect service is special - it's a WebSocket client, not a port listener
    if service_name == "connect":
        proc = find_connect_process()
        if not proc:
            return "❌ Connect is not available"
        # Connect has a process, continue to get details below
        status = "running"
        port = None  # Connect doesn't use a port
    else:
        status, proc = check_service_status(port)

        if status == "not_available":
            # For Kokoro, check if it's in the process of starting up
            if service_name == "kokoro":
                startup_status = is_kokoro_starting_up()
                if startup_status:
                    return f"⏳ Kokoro is {startup_status}"
            return f"❌ {service_name.capitalize()} is not available"
        elif status == "forwarded":
            return f"""🔄 {service_name.capitalize()} is available via port forwarding
   Port: {port} (forwarded)
   Local process: Not running
   Remote: Accessible"""
    
    try:
        with proc.oneshot():
            cpu_percent = proc.cpu_percent(interval=0.1)
            memory_info = proc.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            create_time = proc.create_time()
            cmdline = proc.cmdline()
        
        # Calculate uptime
        uptime_seconds = time.time() - create_time
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        seconds = int(uptime_seconds % 60)
        
        if hours > 0:
            uptime_str = f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            uptime_str = f"{minutes}m {seconds}s"
        else:
            uptime_str = f"{seconds}s"
        
        # Service-specific info via registry
        extra_info_parts = []

        if config.status_info_fn:
            try:
                from voice_mode.services.helpers import call_function_by_path
                extra_info_parts = call_function_by_path(config.status_info_fn, proc, cmdline)
            except Exception as e:
                logger.debug(f"Error calling status_info_fn: {e}")

        extra_info = ""
        if extra_info_parts:
            extra_info = "\n   " + "\n   ".join(extra_info_parts)

        # Build output - connect doesn't have a port
        port_line = f"\n   Port: {port}" if port is not None else ""

        return f"""✅ {service_name.capitalize()} is running locally
   PID: {proc.pid}{port_line}
   CPU: {cpu_percent:.1f}%
   Memory: {memory_mb:.1f} MB
   Uptime: {uptime_str}{extra_info}"""
        
    except Exception as e:
        logger.error(f"Error getting process info: {e}")
        return f"{service_name.capitalize()} is running (PID: {proc.pid}) but could not get details"


async def start_service(service_name: str) -> str:
    """Start a service."""
    config = get_service_config(service_name)
    port = config.port

    # Mutual exclusion: stop other services of same type (TTS/STT only)
    if config.type in (ServiceType.TTS, ServiceType.STT):
        active = get_active_services_by_type(config.type)
        stopped = []
        for other in active:
            if other != service_name:
                await stop_service(other)
                stopped.append(other)
        if stopped:
            logger.info(f"Stopped {', '.join(stopped)} (switching {config.type.value})")

    # Check if already running - connect uses process detection, others use port
    if service_name == "connect":
        if find_connect_process():
            return "Connect is already running"
        port = None
    else:
        if find_process_by_port(port):
            return f"{service_name.capitalize()} is already running on port {port}"

    system = platform.system()

    # Get file name from registry (template_name is used for file naming)
    file_name = config.template_name

    # Helper to check if service is running
    def is_service_running():
        if service_name == "connect":
            return find_connect_process() is not None
        return find_process_by_port(port) is not None

    # Check if managed by service manager
    if system == "Darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.voicemode.{file_name}.plist"
        if plist_path.exists():
            # Use launchctl load
            result = subprocess.run(
                ["launchctl", "load", str(plist_path)],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # Wait for service to start
                for _ in range(10):
                    if is_service_running():
                        return f"✅ {service_name.capitalize()} started"
                    await asyncio.sleep(0.5)
                if service_name == "connect":
                    return f"⚠️ {service_name.capitalize()} loaded but not yet detected as running"
                return f"⚠️ {service_name.capitalize()} loaded but not yet listening on port {port}"
            else:
                error = result.stderr or result.stdout
                if "already loaded" in error.lower():
                    # Service is loaded but maybe not running - try to start it
                    # This can happen if the service crashed
                    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.voicemode.{file_name}"], capture_output=True)
                    await asyncio.sleep(2)
                    if is_service_running():
                        return f"✅ {service_name.capitalize()} restarted"
                    return f"⚠️ {service_name.capitalize()} is loaded but failed to start"
                return f"❌ Failed to start {service_name}: {error}"

    elif system == "Linux":
        service_unit = f"voicemode-{file_name}.service"
        service_file = Path.home() / ".config" / "systemd" / "user" / service_unit
        if service_file.exists():
            # Use systemctl start
            result = subprocess.run(
                ["systemctl", "--user", "start", service_unit],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # Wait for service to start
                for _ in range(10):
                    if is_service_running():
                        return f"✅ {service_name.capitalize()} started"
                    await asyncio.sleep(0.5)
                if service_name == "connect":
                    return f"⚠️ {service_name.capitalize()} started but not yet detected as running"
                return f"⚠️ {service_name.capitalize()} started but not yet listening on port {port}"
            else:
                error = result.stderr or result.stdout
                return f"❌ Failed to start {service_name}: {error}"
    
    # Fallback to direct process start via registry
    from voice_mode.services.helpers import call_function_by_path, get_generic_start_cmd

    if config.start_cmd_fn:
        cmd, cwd, error = call_function_by_path(config.start_cmd_fn, config)
    else:
        # Use generic start command for user-defined services
        cmd, cwd, error = get_generic_start_cmd(config)

    if error:
        return f"❌ {error}"

    try:
        # Start the process

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd
        )

        # Wait a moment to check if it started
        await asyncio.sleep(2)

        if process.poll() is not None:
            # Process exited
            stderr = process.stderr.read().decode() if process.stderr else ""
            return f"❌ {service_name.capitalize()} failed to start: {stderr}"

        # Verify it's running - connect uses process detection, others use port
        if service_name == "connect":
            if find_connect_process():
                return f"✅ {service_name.capitalize()} started successfully (PID: {process.pid})"
            else:
                return f"⚠️ {service_name.capitalize()} process started but not yet detected as running"
        else:
            if find_process_by_port(port):
                return f"✅ {service_name.capitalize()} started successfully (PID: {process.pid})"
            else:
                return f"⚠️ {service_name.capitalize()} process started but not listening on port {port} yet"
            
    except Exception as e:
        logger.error(f"Error starting {service_name}: {e}")
        return f"❌ Error starting {service_name}: {str(e)}"


async def stop_service(service_name: str) -> str:
    """Stop a service."""
    config = get_service_config(service_name)
    port = config.port

    # Connect uses process detection, others use port (port=0 means no port)
    if service_name == "connect":
        port = None
    system = platform.system()

    # Get file name from registry (template_name is used for file naming)
    file_name = config.template_name

    # Check if managed by service manager
    if system == "Darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.voicemode.{file_name}.plist"
        if plist_path.exists():
            # Use launchctl unload (without -w to preserve enable state)
            result = subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return f"✅ {service_name.capitalize()} stopped"
            else:
                error = result.stderr or result.stdout
                # If service is not loaded, that's ok - it's already stopped
                if "could not find specified service" in error.lower():
                    return f"{service_name.capitalize()} is not running"
                return f"❌ Failed to stop {service_name}: {error}"

    elif system == "Linux":
        service_unit = f"voicemode-{file_name}.service"
        service_file = Path.home() / ".config" / "systemd" / "user" / service_unit
        if service_file.exists():
            # Use systemctl stop
            result = subprocess.run(
                ["systemctl", "--user", "stop", service_unit],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return f"✅ {service_name.capitalize()} stopped"
            else:
                error = result.stderr or result.stdout
                return f"❌ Failed to stop {service_name}: {error}"

    # Fallback to process termination - connect uses process detection
    if service_name == "connect":
        proc = find_connect_process()
    else:
        proc = find_process_by_port(port)
    if not proc:
        return f"{service_name.capitalize()} is not running"
    
    try:
        pid = proc.pid
        proc.terminate()
        
        # Wait for graceful shutdown
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            # Force kill if needed
            proc.kill()
            proc.wait(timeout=5)
        
        return f"✅ {service_name.capitalize()} stopped (was PID: {pid})"
        
    except Exception as e:
        logger.error(f"Error stopping {service_name}: {e}")
        return f"❌ Error stopping {service_name}: {str(e)}"


async def restart_service(service_name: str) -> str:
    """Restart a service."""
    stop_result = await stop_service(service_name)
    
    # Brief pause between stop and start
    await asyncio.sleep(1)
    
    start_result = await start_service(service_name)
    
    return f"Restart {service_name}:\n{stop_result}\n{start_result}"


async def enable_service(service_name: str) -> str:
    """Enable a service to start at boot/login.

    Uses create_service_file() as single source of truth for service file generation.
    Uses the service registry for validation and auto-install, enabling user-defined
    services without code changes.
    """
    from voice_mode.services.helpers import call_function_by_path, validate_generic_pre_enable

    system = platform.system()
    config = get_service_config(service_name)

    try:
        # Create service file using the unified function
        service_path, content = create_service_file(service_name)

        # Validate required components exist
        config_vars = get_service_config_vars(service_name)

        # Run pre-enable validation via registry
        validation_error = None
        if config.pre_enable_fn:
            validation_error = call_function_by_path(config.pre_enable_fn, config_vars)
        else:
            # Use generic validation for user-defined services
            validation_error = validate_generic_pre_enable(config_vars)

        # If validation failed and we have auto-install capability, try it
        if validation_error and config.auto_install_fn:
            install_result = await call_function_by_path(config.auto_install_fn)
            if not install_result.get("success"):
                return f"❌ {validation_error} Auto-install failed: {install_result.get('error', 'Unknown error')}"
            start_script = install_result.get("start_script", "")
            logger.info(f"Auto-installed start script at {start_script}")
            # Clear the validation error since auto-install succeeded
            validation_error = None

        if validation_error:
            return f"❌ {validation_error}"

        # Create parent directories
        service_path.parent.mkdir(parents=True, exist_ok=True)

        # Write service file
        service_path.write_text(content)

        if system == "Darwin":
            # Load with launchctl
            result = subprocess.run(
                ["launchctl", "load", "-w", str(service_path)],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                return f"✅ {service_name.capitalize()} service enabled. It will start automatically at login.\nPlist: {service_path}"
            else:
                error = result.stderr or result.stdout
                return f"❌ Failed to enable {service_name} service: {error}"

        else:  # Linux
            # Get file name from registry (template_name is used for file naming)
            svc_config = get_service_config(service_name)
            file_name = svc_config.template_name
            service_unit = f"voicemode-{file_name}.service"

            # Reload and enable systemd
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            result = subprocess.run(
                ["systemctl", "--user", "enable", service_unit],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                # Also start it now
                subprocess.run(["systemctl", "--user", "start", service_unit], check=True)
                return f"✅ {service_name.capitalize()} service enabled and started.\nService: {service_path}"
            else:
                error = result.stderr or result.stdout
                return f"❌ Failed to enable {service_name} service: {error}"

    except Exception as e:
        logger.error(f"Error enabling {service_name} service: {e}")
        return f"❌ Error enabling {service_name} service: {str(e)}"


async def disable_service(service_name: str) -> str:
    """Disable a service from starting at boot/login."""
    system = platform.system()

    # Get file name from registry (template_name is used for file naming)
    config = get_service_config(service_name)
    file_name = config.template_name

    try:
        if system == "Darwin":
            plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.voicemode.{file_name}.plist"

            if not plist_path.exists():
                return f"{service_name.capitalize()} service is not installed"

            # Unload with launchctl
            result = subprocess.run(
                ["launchctl", "unload", "-w", str(plist_path)],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                # Remove the plist file
                plist_path.unlink()
                return f"✅ {service_name.capitalize()} service disabled and removed"
            else:
                error = result.stderr or result.stdout
                # If already unloaded, just remove the file
                if "Could not find specified service" in error:
                    plist_path.unlink()
                    return f"✅ {service_name.capitalize()} service was already disabled, plist removed"
                return f"❌ Failed to disable {service_name} service: {error}"

        else:  # Linux
            service_unit = f"voicemode-{file_name}.service"

            # Stop and disable
            subprocess.run(["systemctl", "--user", "stop", service_unit], check=True)
            result = subprocess.run(
                ["systemctl", "--user", "disable", service_unit],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                # Remove service file
                service_path = Path.home() / ".config" / "systemd" / "user" / service_unit
                if service_path.exists():
                    service_path.unlink()

                # Reload systemd
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)

                return f"✅ {service_name.capitalize()} service disabled and removed"
            else:
                error = result.stderr or result.stdout
                return f"❌ Failed to disable {service_name} service: {error}"
                
    except Exception as e:
        logger.error(f"Error disabling {service_name} service: {e}")
        return f"❌ Error disabling {service_name} service: {str(e)}"


async def view_logs(service_name: str, lines: Optional[int] = None) -> str:
    """View service logs."""
    system = platform.system()
    lines = lines or 50

    # Get log name from registry (fallback to template_name if log_name not set)
    config = get_service_config(service_name)
    log_name = config.log_name or config.template_name

    try:
        if system == "Darwin":
            # Use log show command with process_name from registry
            process_name = config.process_name or f"{service_name}-server"
            cmd = [
                "log", "show",
                "--predicate", f'process == "{process_name}"',
                "--last", f"{lines}",
                "--style", "compact"
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0 and result.stdout.strip():
                return f"=== Last {lines} log entries for {service_name} ===\n{result.stdout}"
            else:
                # Fallback to log files (voicemode logs to serve/ directory)
                log_dir = Path.home() / ".voicemode" / "logs" / log_name
                out_log = log_dir / f"{log_name}.out.log"
                err_log = log_dir / f"{log_name}.err.log"

                logs = []
                if out_log.exists():
                    with open(out_log) as f:
                        stdout_lines = f.readlines()[-lines:]
                        if stdout_lines:
                            logs.append(f"=== stdout (last {len(stdout_lines)} lines) ===")
                            logs.extend(stdout_lines)

                if err_log.exists():
                    with open(err_log) as f:
                        stderr_lines = f.readlines()[-lines:]
                        if stderr_lines:
                            if logs:
                                logs.append("")
                            logs.append(f"=== stderr (last {len(stderr_lines)} lines) ===")
                            logs.extend(stderr_lines)

                if logs:
                    return "".join(logs).rstrip()
                else:
                    return f"No logs found for {service_name}"

        else:  # Linux
            # Use journalctl
            cmd = [
                "journalctl", "--user",
                "-u", f"voicemode-{log_name}.service",
                "-n", str(lines),
                "--no-pager"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                return f"=== Last {lines} journal entries for {service_name} ===\n{result.stdout}"
            else:
                return f"Failed to retrieve logs: {result.stderr}"
                
    except Exception as e:
        logger.error(f"Error viewing logs for {service_name}: {e}")
        return f"❌ Error viewing logs: {str(e)}"


@mcp.tool()
async def service(
    service_name: str,
    action: Literal["status", "start", "stop", "restart", "enable", "disable", "logs"] = "status",
    lines: Optional[Union[int, str]] = None
) -> str:
    """Unified service management tool for voice mode services.

    Manage Whisper (STT), Kokoro (TTS), VoiceMode (HTTP MCP server), and Connect (standby) services.

    Built-in services: whisper, kokoro, voicemode, connect.
    User-defined services in ~/.voicemode/services.yaml are also supported.

    Args:
        service_name: The service to manage (e.g., "whisper", "kokoro", "voicemode", "connect",
            or any user-defined service from ~/.voicemode/services.yaml)
        action: The action to perform (default: "status")
            - status: Show if service is running and resource usage
            - start: Start the service
            - stop: Stop the service
            - restart: Stop and start the service
            - enable: Configure service to start at boot/login
            - disable: Remove service from boot/login
            - logs: View recent service logs
        lines: Number of log lines to show (only for logs action, default: 50)

    Returns:
        Status message indicating the result of the action

    Examples:
        service("whisper", "status")  # Check if Whisper is running
        service("kokoro", "start")    # Start Kokoro service
        service("voicemode", "start") # Start VoiceMode HTTP MCP server
        service("connect", "enable")  # Enable Connect standby service for remote wake
        service("whisper", "logs", 100)  # View last 100 lines of Whisper logs
    """
    # Validate service name against registry
    try:
        _ = get_service_config(service_name)
    except ValueError as e:
        return f"❌ {e}"

    # Convert lines to integer if provided as string
    if lines is not None and isinstance(lines, str):
        try:
            lines = int(lines)
        except ValueError:
            logger.warning(f"Invalid lines value '{lines}', using default 50")
            lines = 50
    
    # Route to appropriate handler
    if action == "status":
        return await status_service(service_name)
    elif action == "start":
        return await start_service(service_name)
    elif action == "stop":
        return await stop_service(service_name)
    elif action == "restart":
        return await restart_service(service_name)
    elif action == "enable":
        return await enable_service(service_name)
    elif action == "disable":
        return await disable_service(service_name)
    elif action == "logs":
        return await view_logs(service_name, lines)
    else:
        return f"❌ Unknown action: {action}"


async def install_service(service_name: str) -> Dict[str, Any]:
    """Install service files for a service."""
    try:
        system = platform.system()
        config_vars = get_service_config_vars(service_name)

        # Load template
        template_content = load_service_template(service_name)

        # Replace placeholders
        for key, value in config_vars.items():
            template_content = template_content.replace(f"{{{key}}}", str(value))

        # Get file name from registry (template_name is used for file naming)
        svc_config = get_service_config(service_name)
        file_name = svc_config.template_name

        if system == "Darwin":
            # Install launchd plist
            plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.voicemode.{file_name}.plist"
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            plist_path.write_text(template_content)
            return {"success": True, "service_file": str(plist_path)}
        else:
            # Install systemd service
            service_path = Path.home() / ".config" / "systemd" / "user" / f"voicemode-{file_name}.service"
            service_path.parent.mkdir(parents=True, exist_ok=True)
            service_path.write_text(template_content)

            # Reload systemd
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            return {"success": True, "service_file": str(service_path)}

    except Exception as e:
        logger.error(f"Error installing service {service_name}: {e}")
        return {"success": False, "error": str(e)}


async def uninstall_service(service_name: str) -> Dict[str, Any]:
    """Remove service files for a service."""
    try:
        system = platform.system()

        # Get file name from registry (template_name is used for file naming)
        config = get_service_config(service_name)
        file_name = config.template_name

        if system == "Darwin":
            plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.voicemode.{file_name}.plist"
            if plist_path.exists():
                plist_path.unlink()
                return {"success": True, "message": f"Removed {plist_path}"}
            else:
                return {"success": True, "message": "Service file not found"}
        else:
            service_path = Path.home() / ".config" / "systemd" / "user" / f"voicemode-{file_name}.service"
            if service_path.exists():
                service_path.unlink()
                # Reload systemd
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
                return {"success": True, "message": f"Removed {service_path}"}
            else:
                return {"success": True, "message": "Service file not found"}

    except Exception as e:
        logger.error(f"Error uninstalling service {service_name}: {e}")
        return {"success": False, "error": str(e)}
