"""Explicit user retry requests consumed by the installed daemon, not a second sender."""

import json
import os
from pathlib import Path
import time
import uuid

from .storage import RecoveryError, THREAD_RE, atomic_json, config


def submit_retry(data, thread_id, turn_id, clock=time.time):
    if any(
        not isinstance(value, str) or not THREAD_RE.fullmatch(value)
        for value in (thread_id, turn_id)
    ):
        raise RecoveryError("invalid_thread_or_turn_id")
    cfg = config(data)
    if not cfg["enabled"]:
        raise RecoveryError("retry_disabled")
    identifier = str(uuid.uuid4())
    request = {
        "request_id": identifier,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "requested_at": clock(),
        "enabled_epoch": cfg.get("enabled_epoch"),
    }
    atomic_json(data / "requests" / (identifier + ".request.json"), request)
    return dict(request, result="submitted_not_dispatched")


def process_requests(data, engine, persist):
    paths = (data / "requests").glob("*.request.json")
    paths = sorted(
        p for p in paths if THREAD_RE.fullmatch(p.name.removesuffix(".request.json"))
    )
    for path in paths[:10]:
        identifier = path.name.removesuffix(".request.json")
        result_path = path.with_name(identifier + ".result.json")
        if result_path.exists():
            path.unlink()
            continue
        try:
            if (
                path.is_symlink()
                or path.stat().st_uid != os.getuid()
                or path.stat().st_size > 4096
            ):
                raise ValueError("invalid_request_file")
            request = json.loads(path.read_text())
            if not isinstance(request, dict) or request.get("request_id") != identifier:
                raise ValueError("invalid_request")
            result = engine.request_retry(request)
            persist()
        except (ValueError, TypeError, KeyError):
            result = "invalid_request"
        atomic_json(
            result_path,
            {"request_id": identifier, "result": result, "at": engine.clock()},
        )
        path.unlink()
