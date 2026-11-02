"""Load an existing local thread through Codex's registered macOS URL handler."""

import subprocess
import sys

from .storage import RecoveryError, THREAD_RE

OWNER_UNAVAILABLE_ERRORS = frozenset(
    {
        "desktop_owner_unavailable",
        "ipc_no-client-found",
        "ipc_transport_unavailable",
    }
)


def activate_desktop_thread(thread_id):
    if not isinstance(thread_id, str) or not THREAD_RE.fullmatch(thread_id):
        raise RecoveryError("invalid_thread_id")
    if sys.platform != "darwin":
        raise RecoveryError("macos_required")
    try:
        result = subprocess.run(
            [
                "/usr/bin/open",
                "-g",
                "-b",
                "com.openai.codex",
                "codex://threads/" + thread_id + "?hostId=local",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryError("desktop_activation_failed") from exc
    if result.returncode:
        raise RecoveryError("desktop_activation_failed")
