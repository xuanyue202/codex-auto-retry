"""User LaunchAgent lifecycle shared by installation and controls."""

import os
from pathlib import Path
import re
import subprocess
import time

from .storage import DATA, RecoveryError

LABEL = "com.local.codex-capacity-retry"


def service_target():
    return f"gui/{os.getuid()}/{LABEL}"


def agent_path():
    return Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")


def launch(*args):
    return subprocess.run(
        ["launchctl", *args], capture_output=True, text=True, timeout=15
    )


def service_info():
    result = launch("print", service_target())
    match = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.M)
    return {
        "loaded": result.returncode == 0,
        "pid": int(match.group(1)) if match else None,
    }


def owned_agent(plist, data=None):
    data = DATA if data is None else data
    args = plist.get("ProgramArguments", [])
    if plist.get("Label") != LABEL or len(args) != 3 or args[-1] != "run":
        return False
    script = Path(args[1])
    return script == data / "runtime/retry_watchdog.py" or (
        script.name == "retry_watchdog.py" and script.parent.parent == data / "releases"
    )


def stop_service():
    if not service_info()["loaded"]:
        return
    if launch("bootout", service_target()).returncode:
        raise RecoveryError("launchagent_stop_failed")


def start_service():
    if launch("bootstrap", f"gui/{os.getuid()}", str(agent_path())).returncode:
        raise RecoveryError("launchagent_start_failed")


def healthy_installation(state, cfg, started, now):
    health = state.get("health", {})
    return (
        health.get("enabled") is cfg["enabled"]
        and state.get("enabled_epoch") == cfg.get("enabled_epoch")
        and started <= health.get("heartbeat", 0) <= now
        and now - health.get("heartbeat", 0) < 15
    )


def status(cfg, state, now=None):
    now = time.time() if now is None else now
    health = state.get("health", {})
    service = service_info()
    alive = False
    if service["pid"] and service["pid"] == health.get("pid"):
        try:
            os.kill(service["pid"], 0)
            alive = True
        except (ProcessLookupError, PermissionError):
            pass
    age = now - health.get("heartbeat", 0)
    threads = state.get("threads", {})
    return {
        "enabled": cfg["enabled"],
        "loaded": service["loaded"],
        "daemon_alive": alive,
        "heartbeat_fresh": 0 <= age < 15,
        "health": health,
        "operation": state.get("operation"),
        "pending_count": len(state.get("jobs", {})),
        "awaiting_start_count": sum(
            bool(t.get("awaiting_start")) for t in threads.values()
        ),
        "delays_seconds": cfg["delays_seconds"],
        "max_attempts": cfg["max_attempts"],
        "max_outage_seconds": cfg["max_outage_seconds"],
        "last_results": {
            tid: {
                "dispatch": thread.get("last_dispatch"),
                "decision": thread.get("last_recovery_decision"),
                "stopped_reason": thread.get("stopped_reason"),
            }
            for tid, thread in threads.items()
            if thread.get("last_dispatch") or thread.get("stopped_reason")
        },
    }
