"""Read-only compatibility checks: never start a model turn."""

import sys

from .ipc import DesktopIPC
from .storage import RecoveryError, THREAD_RE


def check(cfg, thread=None):
    if sys.platform != "darwin":
        raise RecoveryError("macos_required")
    if thread is not None and not THREAD_RE.fullmatch(thread):
        raise RecoveryError("invalid_thread_id")
    client = DesktopIPC(cfg["socket_path"])
    try:
        result = {
            "ipc_initialize": True,
            "thread_owner_checked": False,
            "mutating_requests": 0,
        }
        if thread:
            owner = client.owner(thread)
            settings = client.settings(thread, owner)
            if settings.get("mode") not in (None, "default", "durable"):
                raise RecoveryError("unsupported_thread_mode")
            result.update(thread_owner_checked=True, owner_available=True)
        return result
    finally:
        client.close()
