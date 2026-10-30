#!/usr/bin/env python3
"""Inject overload metadata in isolation, then continue this real thread once.

Never alter real rollouts. Successful injection is not a real provider outage.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

SOURCE = Path(__file__).resolve().parent
DATA = Path.home() / "Library/Application Support/CodexAutoRetryMac"
import retry_watchdog as w
from install import source_manifest


def installed_runtime():
    receipt = w.load_json(DATA / "installation-receipt.json", {})
    runtime = Path(receipt.get("runtime", DATA / "runtime"))
    expected = source_manifest()
    actual = {name: digest(runtime / name) for name in expected["files"]}
    if actual != expected["files"]:
        raise w.RecoveryError("source_and_installed_release_differ")
    return runtime / "retry_watchdog.py"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def context_metadata(payload):
    policy = {
        k: v
        for k, v in payload.items()
        if "permission" in k
        or "sandbox" in k
        or "approval" in k
        or k in ("network_access", "writable_roots")
    }
    return {
        "cwd": payload.get("cwd"),
        "model": payload.get("model"),
        "effort": payload.get("effort"),
        "mode": (payload.get("collaboration_mode") or {}).get("mode"),
        "policy_fields": sorted(policy),
        "policy_sha256": hashlib.sha256(
            json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def event_time(event):
    return dt.datetime.fromisoformat(
        event["timestamp"].replace("Z", "+00:00")
    ).timestamp()


def settings_compatible(before, selected):
    # An unset selector inherits the existing resolved effort; verify the actual
    # resumed turn still has precisely the same resolved context afterwards.
    return selected.get("model") == before["model"] and selected.get(
        "reasoningEffort"
    ) in (None, before["effort"])


def snapshot(tid):
    """Merge lifecycle metadata across compaction shards of the exact thread."""
    events, contexts, assistant_turns, paths = [], {}, set(), []
    for path in sorted((w.CODEX / "sessions").glob("*/*/*/*" + tid + "*.jsonl")):
        if w.thread_id(path) != tid:
            continue
        paths.append(str(path))
        current = None
        with path.open("rb") as stream:
            for raw in stream:
                if not raw.endswith(b"\n"):
                    continue
                if w.EVENT_RE.search(raw[:180]):
                    event = w.metadata(raw)
                    if event:
                        events.append(event)
                        if event["kind"] == "task_started":
                            current = event["turn_id"]
                elif re.search(rb'"type"\s*:\s*"turn_context"', raw[:180]):
                    obj = json.loads(raw)
                    payload = obj["payload"]
                    contexts[payload["turn_id"]] = context_metadata(payload)
                elif (
                    current
                    and re.search(rb'"type"\s*:\s*"response_item"', raw[:180])
                    and re.search(rb'"role"\s*:\s*"assistant"', raw[:600])
                ):
                    assistant_turns.add(current)
    events.sort(key=event_time)
    latest = w.fold_lifecycle(events)
    return {
        "events": events,
        "latest": latest,
        "contexts": contexts,
        "assistant_turns": assistant_turns,
        "paths": paths,
    }


def healthy_daemon(tid):
    cfg = w.config(DATA)
    state = w.load_json(DATA / "state.json", {})
    health = state.get("health", {})
    loaded = (
        subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.local.codex-capacity-retry"],
            capture_output=True,
        ).returncode
        == 0
    )
    return {
        "enabled": cfg["enabled"],
        "loaded": loaded,
        "pid": health.get("pid"),
        "heartbeat_age_s": time.time() - health.get("heartbeat", 0),
        "health_enabled": health.get("enabled"),
        "epoch_matches": state.get("enabled_epoch") == cfg.get("enabled_epoch"),
        "epoch": cfg.get("enabled_epoch"),
        "pending_this_thread": tid in state.get("jobs", {}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread-id", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--after-turn")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--synthetic-goal-status", choices=["active"])
    args = parser.parse_args()
    if not args.thread_id or not w.THREAD_RE.fullmatch(args.thread_id):
        raise SystemExit("A valid current test thread ID is required")
    if args.inspect:
        current = snapshot(args.thread_id)
        print(
            json.dumps(
                {
                    "thread_id": args.thread_id,
                    "latest": current["latest"],
                    "health": healthy_daemon(args.thread_id),
                },
                indent=2,
            )
        )
        return
    runtime = installed_runtime()
    if not args.after_turn or not args.evidence:
        raise SystemExit("--after-turn and --evidence are required for a one-shot test")
    if not 30 <= args.timeout <= 600 or args.evidence.exists():
        raise SystemExit("invalid timeout or evidence already exists")
    args.evidence.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (SOURCE / "evidence/capacity-e2e-local.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    tid, original = args.thread_id, args.after_turn
    cfg = w.config(DATA)
    evidence = {
        "mode": "synthetic_mirror_not_real_provider_overload",
        "thread_id": tid,
        "original_turn_id": original,
        "dispatch_attempts": 0,
        "error_code": "server_overloaded",
        "synthetic_goal_status": args.synthetic_goal_status,
        "source_sha256": digest(SOURCE / "retry_watchdog.py"),
        "runtime_sha256": digest(runtime),
        "started_at_unix": time.time(),
    }

    def record(phase, **fields):
        evidence.update(phase=phase, updated_at_unix=time.time(), **fields)
        w.atomic_json(args.evidence, evidence)

    start = time.monotonic()
    baseline = snapshot(tid)
    before = baseline["contexts"].get(original)
    health = healthy_daemon(tid)
    if (
        not before
        or baseline["latest"].get("kind") != "task_started"
        or baseline["latest"].get("turn_id") != original
        or evidence["source_sha256"] != evidence["runtime_sha256"]
        or tid in cfg["exclude_thread_ids"]
        or not all(
            health[k] for k in ("enabled", "loaded", "health_enabled", "epoch_matches")
        )
        or health["heartbeat_age_s"] >= 15
        or health["pending_this_thread"]
    ):
        record("refused_preflight", daemon=health)
        return
    record(
        "waiting_for_original_turn_completion",
        original_settings=before,
        original_rollout_paths=baseline["paths"],
        daemon_before=health,
        poll_seconds=cfg["poll_seconds"],
        delays_seconds=cfg["delays_seconds"],
    )

    def eligible_real():
        latest = snapshot(tid)["latest"]
        state = healthy_daemon(tid)
        return (
            latest.get("kind") == "task_complete"
            and latest.get("turn_id") == original
            and not latest.get("has_error")
            and latest.get("goal_status") is None
            and state["enabled"]
            and state["loaded"]
            and state["health_enabled"]
            and state["epoch"] == health["epoch"]
            and state["epoch_matches"]
            and state["heartbeat_age_s"] < 15
            and not state["pending_this_thread"]
            and digest(runtime) == evidence["runtime_sha256"]
        )

    while time.monotonic() - start < args.timeout:
        latest = snapshot(tid)["latest"]
        if latest.get("turn_id") != original or latest.get("kind") in (
            "turn_aborted",
            "user_message",
        ):
            record("cancelled_by_new_input_or_interruption")
            return
        if latest.get("kind") == "task_complete":
            if latest.get("has_error"):
                record(
                    "real_error_observed_no_injection",
                    real_capacity=latest.get("capacity"),
                )
                return
            break
        time.sleep(0.5)
    else:
        record("timed_out_waiting_for_original")
        return
    if not eligible_real():
        record("refused_real_thread_not_idle")
        return

    try:
        with tempfile.TemporaryDirectory(
            prefix="capacity-mirror-", dir=args.evidence.parent
        ) as tmp:
            mirror_root = Path(tmp)
            assert not mirror_root.is_relative_to(Path(cfg["sessions_root"]))
            mirror = (
                mirror_root
                / dt.datetime.now(dt.timezone.utc).strftime("%Y/%m/%d")
                / ("rollout-" + tid + ".jsonl")
            )
            mirror.parent.mkdir(parents=True)
            mirror.touch()
            engine = w.Engine(dict(cfg, sessions_root=str(mirror_root)))
            engine.scan()
            time.sleep(
                cfg["poll_seconds"]
            )  # Native timestamps have millisecond precision.
            stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")

            def wire(kind, **extra):
                return (
                    json.dumps(
                        {
                            "timestamp": stamp,
                            "type": "event_msg",
                            "payload": {"type": kind, "turn_id": original, **extra},
                        }
                    )
                    + "\n"
                ).encode()

            with mirror.open("ab") as stream:
                if args.synthetic_goal_status:
                    stream.write(
                        wire(
                            "thread_goal_updated",
                            goal={"status": args.synthetic_goal_status},
                        )
                    )
                stream.write(wire("task_started"))
                stream.write(
                    wire(
                        "task_complete", error={"codex_error_info": "server_overloaded"}
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
            injected = time.time()
            time.sleep(cfg["poll_seconds"])
            engine.scan()
            job = engine.state["jobs"].get(tid)
            if not job or job["turn_id"] != original:
                record("failed_error_not_queued")
                return
            record(
                "queued_from_structured_error",
                injected_at_unix=injected,
                detected_at_unix=time.time(),
                queue_due_unix=job["due"],
                queue_delay_s=job["due"] - engine.state["threads"][tid]["updated"],
                mirror_outside_production_sessions=True,
                concurrent_dispatch_prevention="real_turn_succeeded_no_pending_mirror_invisible_to_daemon",
            )
            while time.time() < job["due"]:
                time.sleep(min(0.1, max(0, job["due"] - time.time())))

            class CountingIPC(w.DesktopIPC):
                def settings(self, thread_id, owner_id):
                    result = super().settings(thread_id, owner_id)
                    record(
                        "owner_and_settings_readable",
                        owner_found=True,
                        owner_model=result.get("model"),
                        owner_effort=result.get("reasoningEffort"),
                    )
                    if not settings_compatible(before, result):
                        raise w.RecoveryError("settings_drift_before_dispatch")
                    return result

                def continue_thread(self, thread_id, owner_id, model, effort):
                    evidence["dispatch_attempts"] += 1
                    record("dispatch_attempt_recorded", dispatch_at_unix=time.time())
                    return super().continue_thread(thread_id, owner_id, model, effort)

            def persist():
                w.atomic_json(args.evidence.with_suffix(".intent.json"), engine.state)

            result = engine.dispatch(
                tid, persist, ipc_factory=CountingIPC, may_dispatch=eligible_real
            )
            record("dispatch_" + result, dispatch_result=result)
            if result != "acknowledged" or evidence["dispatch_attempts"] != 1:
                return
            while time.monotonic() - start < args.timeout:
                current = snapshot(tid)
                completed = [
                    e
                    for e in current["events"]
                    if e["kind"] == "task_complete" and e["turn_id"] == original
                ]
                if not completed:
                    record("failed_missing_real_completion")
                    return
                after = [
                    e
                    for e in current["events"]
                    if event_time(e) > event_time(completed[-1])
                ]
                if any(e["kind"] in ("user_message", "turn_aborted") for e in after):
                    record("inconclusive_external_input_after_dispatch")
                    return
                starts = [
                    e["turn_id"]
                    for e in after
                    if e["kind"] == "task_started" and e["turn_id"] != original
                ]
                for new_id in starts:
                    context = current["contexts"].get(new_id)
                    if context and new_id in current["assistant_turns"]:
                        same = context == before
                        native_start = next(
                            e
                            for e in after
                            if e["kind"] == "task_started" and e["turn_id"] == new_id
                        )
                        engine.event(tid, Path(current["paths"][-1]), native_start)
                        persist()
                        record(
                            (
                                "same_thread_resumed"
                                if same
                                else "failed_settings_changed"
                            ),
                            resumed_turn_id=new_id,
                            resumed_settings=context,
                            settings_identical=same,
                            assistant_message_observed=True,
                            new_turn_ids_observed=starts,
                            real_rollout_paths=current["paths"],
                            delivery_observation=engine.state["threads"][tid].get(
                                "last_dispatch"
                            ),
                            daemon_after=healthy_daemon(tid),
                        )
                        return
                time.sleep(0.5)
            record("acknowledged_but_new_assistant_turn_not_observed")
    except Exception as exc:
        record(
            "failed_probe", error_type=type(exc).__name__, error_category=str(exc)[:100]
        )
        raise
    finally:
        evidence["mirror_cleaned"] = (
            "mirror_root" not in locals() or not mirror_root.exists()
        )
        w.atomic_json(args.evidence, evidence)


if __name__ == "__main__":
    main()
