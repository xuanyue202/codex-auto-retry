"""Private state paths, configuration and atomic JSON persistence."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

HOME = Path.home()
DATA = HOME / "Library/Application Support/CodexAutoRetryMac"
CODEX = Path(os.environ.get("CODEX_HOME", str(HOME / ".codex"))).expanduser().resolve()
THREAD_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)
EVENT_RE = re.compile(rb'"type"\s*:\s*"event_msg"')
DEFAULT = {
    "enabled": False,
    "poll_seconds": 1,
    "delays_seconds": [1, 2, 4, 8, 16],
    "max_attempts": 5,
    "max_outage_seconds": 900,
    "retention_days": 7,
    "recover_active_goals": True,
    "start_confirmation_seconds": 30,
    "socket_path": str(CODEX / "ipc/ipc.sock"),
    "sessions_root": str(CODEX / "sessions"),
    "exclude_thread_ids": [],
    "scope": "desktop_owned_new_capacity_failures_only",
}


class RecoveryError(Exception):
    pass


class AmbiguousDispatch(RecoveryError):
    """The request may have been accepted; never blindly send it twice."""


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    with os.fdopen(
        os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def config(data):
    cfg = dict(DEFAULT)
    cfg.update(load_json(data / "config.json", {}))
    if not isinstance(cfg["enabled"], bool):
        raise RecoveryError("invalid_enabled_setting")

    def number(value, lower, upper, integer=False):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and lower <= value <= upper
            and (not integer or isinstance(value, int))
        )

    if not number(cfg["max_attempts"], 1, 10, True) or not number(
        cfg["max_outage_seconds"], 30, 3600
    ):
        raise RecoveryError("invalid_retry_limits")
    if (
        not isinstance(cfg["delays_seconds"], list)
        or not cfg["delays_seconds"]
        or any(not number(x, 1, 600) for x in cfg["delays_seconds"])
    ):
        raise RecoveryError("invalid_retry_delays")
    if not number(cfg["poll_seconds"], 1, 60):
        raise RecoveryError("invalid_poll_interval")
    if not isinstance(cfg["recover_active_goals"], bool):
        raise RecoveryError("invalid_goal_recovery_setting")
    if not number(cfg["start_confirmation_seconds"], 1, 300):
        raise RecoveryError("invalid_start_confirmation_seconds")
    if not number(cfg["retention_days"], 1, 3650, True):
        raise RecoveryError("invalid_retention_days")
    if not isinstance(cfg["exclude_thread_ids"], list) or any(
        not isinstance(t, str) or not THREAD_RE.fullmatch(t)
        for t in cfg["exclude_thread_ids"]
    ):
        raise RecoveryError("invalid_excluded_threads")
    for name in ("socket_path", "sessions_root"):
        if not isinstance(cfg[name], str) or not Path(cfg[name]).is_absolute():
            raise RecoveryError("invalid_" + name)
    return cfg
