"""Transient rollout parsing; retain lifecycle metadata only."""

import datetime as dt
import hashlib
import json
from pathlib import Path
from .storage import EVENT_RE, THREAD_RE
from .recovery_input import (
    CONTINUATION_TEXT,
    USER_REQUEST_TRIGGERS,
    own_recovery_message,
    recovery_id_from_trigger,
)


def classify_capacity(error):
    """Only exact provider overload metadata, never prompts or quoted text."""
    if not isinstance(error, dict):
        return False
    info = error.get("codex_error_info", error.get("codexErrorInfo", error.get("code")))
    if isinstance(info, dict):
        info = next(iter(info), None) if len(info) == 1 else None
    if info is not None:
        return info in ("server_overloaded", "serverOverloaded")
    return False


def metadata(raw):
    if not EVENT_RE.search(raw[:180]):
        return None
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(obj, dict) or obj.get("type") != "event_msg":
        return None
    p = obj.get("payload", {})
    if not isinstance(p, dict):
        return None
    kind = p.get("type")
    if kind not in (
        "task_started",
        "task_complete",
        "turn_aborted",
        "user_message",
        "thread_goal_updated",
    ):
        return None
    if p.get("turn_id") is not None and not isinstance(p["turn_id"], str):
        return None
    goal = p.get("goal")
    goal_present, revision = None, None
    status = p.get("status")
    if kind == "thread_goal_updated" and "goal" in p:
        goal_present = goal is not None
        if isinstance(goal, dict):
            status = goal.get("status")
            # Store a hash, never the objective. Usage refreshes do not change
            # the user's goal; objective, status and limit edits do.
            semantic = {
                k: v
                for k, v in goal.items()
                if k not in ("tokensUsed", "timeUsedSeconds", "updatedAt")
            }
            revision = hashlib.sha256(
                json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        elif goal is not None:
            status = "unknown"
    if status is not None and not isinstance(status, str):
        status = "unknown"
    # Deliberately discard text, tool contents, last_agent_message and raw errors.
    result = {
        "kind": kind,
        "turn_id": p.get("turn_id"),
        "timestamp": obj.get("timestamp"),
        "capacity": classify_capacity(p.get("error")),
        "has_error": bool(p.get("error")),
        "goal_status": status,
        "goal_present": goal_present,
        "goal_revision": revision,
        "turn_trigger": (
            (p.get("turn_attribution") or {}).get("turn_trigger")
            if isinstance(p.get("turn_attribution"), dict)
            else None
        ),
    }
    if kind == "user_message" and p.get("message") == CONTINUATION_TEXT:
        result["recovery_input"] = True
    return result


def read_records(path, offset=0, max_offset=None):
    """Return complete newline-delimited records only; bound allocations."""
    out = []
    with Path(path).open("rb") as f:
        f.seek(offset)
        while True:
            start = f.tell()
            if max_offset is not None and start >= max_offset:
                break
            limit = 2 * 1024 * 1024 + 1
            if max_offset is not None:
                limit = min(limit, max_offset - start)
            raw = f.readline(limit)
            if not raw:
                break
            if not raw.endswith(b"\n"):
                if len(raw) <= 2 * 1024 * 1024:
                    f.seek(start)
                    break
                # Oversize non-metadata record: drain without decoding content.
                while raw and not raw.endswith(b"\n"):
                    if max_offset is not None and f.tell() >= max_offset:
                        raw = b""
                        break
                    raw = f.readline(
                        65536
                        if max_offset is None
                        else min(65536, max_offset - f.tell())
                    )
                if not raw:
                    f.seek(start)
                    break
                continue
            event = metadata(raw)
            if event:
                out.append(event)
        return out, f.tell()


def thread_id(path):
    name = Path(path).name
    # Current per-turn filenames retain the persistent thread ID first.
    matches = THREAD_RE.findall(name)
    return matches[0].lower() if matches else None


def latest_lifecycle(path):
    events, _ = read_records(path)
    return fold_lifecycle(events)


def fold_lifecycle(events):
    result = {"kind": None, "turn_id": None}
    status, revision, trigger = None, None, None
    has_turn, stopped_during_turn, ordinary_request = False, False, False
    current_turn = None
    cancelled, cancelled_turn = False, None
    recovery_input_seen = False
    seen_records = set()
    for e in events:
        if e.get("timestamp") is not None:
            key = event_key(e)
            if key in seen_records:
                continue
            seen_records.add(key)
        if e["kind"] == "thread_goal_updated":
            new_status = goal_status(e)
            changed = goal_changed(status, revision, e)
            status, revision = new_status, e.get("goal_revision")
            if changed and has_turn:
                stopped_during_turn = status != "active"
                ordinary_request = False
        else:
            if (
                not recovery_input_seen
                and result.get("kind") == "task_started"
                and own_recovery_message(
                    e, current_turn, recovery_id_from_trigger(trigger)
                )
            ):
                recovery_input_seen = True
                continue
            if e["kind"] == "task_started":
                if cancelled and e.get("turn_id") in (None, cancelled_turn):
                    continue
                identifier = recovery_id_from_trigger(e.get("turn_trigger"))
                if (
                    identifier is not None
                    and identifier == recovery_id_from_trigger(trigger)
                    and e.get("turn_id") == current_turn
                ):
                    continue
                recovery_input_seen = False
                cancelled = False
                continuation = result.get("kind") == "task_complete" and result.get(
                    "capacity"
                )
                has_turn, stopped_during_turn = True, False
                trigger = e.get("turn_trigger")
                if trigger in USER_REQUEST_TRIGGERS:
                    ordinary_request = status in (
                        "paused",
                        "complete",
                        "completed",
                        "budget_limited",
                    )
                elif not continuation:
                    ordinary_request = False
                current_turn = e.get("turn_id")
            elif e["kind"] in ("task_complete", "turn_aborted"):
                if e["kind"] == "task_complete" and cancelled:
                    continue
                if current_turn is not None and e.get("turn_id") not in (
                    None,
                    current_turn,
                ):
                    continue
                current_turn = e.get("turn_id") or current_turn
            if e["kind"] in ("user_message", "turn_aborted"):
                cancelled, cancelled_turn = True, current_turn
            result = dict(e)
    effective = status
    if stopped_during_turn:
        effective = "stopped_during_turn"
    elif ordinary_request and status in (
        "paused",
        "complete",
        "completed",
        "budget_limited",
    ):
        # Retry this new explicit human request without reactivating its old Goal.
        effective = None
    result.update(
        goal_status=status,
        goal_revision=revision,
        retry_goal_status=effective,
        turn_trigger=trigger,
    )
    return result


def event_timestamp(event):
    try:
        return dt.datetime.fromisoformat(
            event["timestamp"].replace("Z", "+00:00")
        ).timestamp()
    except (KeyError, AttributeError, ValueError, TypeError):
        return None


def event_key(event):
    return hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def goal_status(event):
    return (
        None
        if event.get("goal_present") is False
        else event.get("goal_status") or "unknown"
    )


def goal_changed(status, revision, event):
    if goal_status(event) != status:
        return True
    if event.get("goal_present") is False:
        return revision is not None
    return event.get("goal_revision") is None or event["goal_revision"] != revision


def latest_thread_lifecycle(root, tid, fallback_path):
    # New turns and goal pause/stop events may be written in a newer compaction
    # shard. A terminal event in the old shard alone cannot authorize a retry.
    paths = {Path(fallback_path)}
    paths.update(
        p for p in Path(root).glob("*/*/*/*" + tid + "*.jsonl") if thread_id(p) == tid
    )
    events = []
    for path in sorted(paths):
        try:
            records, _ = read_records(path)
        except FileNotFoundError:
            continue
        events.extend(records)
    events.sort(
        key=lambda e: (
            event_timestamp(e) if event_timestamp(e) is not None else float("-inf")
        )
    )
    return fold_lifecycle(events)


def goal_allows_retry(cfg, status):
    return status is None or (
        status == "active" and cfg.get("recover_active_goals", True)
    )
