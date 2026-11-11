#!/usr/bin/env python3
"""Bounded same-thread recovery of future Codex Desktop recoverable errors.

IPC architecture adapted from sybxxx/codex-auto-retry (MIT); see NOTICE.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import time
import uuid
from codex_retry.storage import (
    HOME,
    DATA,
    CODEX,
    DEFAULT,
    RecoveryError,
    AmbiguousDispatch,
    atomic_json,
    load_json,
    config,
)
from codex_retry.lifecycle import (
    EVENT_RE,
    THREAD_RE,
    classify_capacity,
    metadata,
    read_records,
    thread_id,
    latest_lifecycle,
    fold_lifecycle,
    event_timestamp,
    event_key,
    goal_status,
    goal_changed,
    latest_thread_lifecycle,
    goal_allows_retry,
)
from codex_retry.ipc import DesktopIPC, safe_socket
from codex_retry.owner_recovery import activate_desktop_thread, OWNER_UNAVAILABLE_ERRORS
from codex_retry.manual_retry import process_requests
from codex_retry.recovery_input import (
    USER_REQUEST_TRIGGERS,
    own_recovery_message,
    recovery_id_from_trigger,
)


class Engine:
    def __init__(self, cfg, state=None, clock=time.time):
        self.cfg, self.clock = cfg, clock
        if state is not None and (
            not isinstance(state, dict)
            or any(
                not isinstance(state.get(k), dict)
                for k in ("files", "threads", "jobs", "seen", "health")
            )
        ):
            raise RecoveryError("invalid_state_schema")
        if state is not None and (
            state.get("version") != 1 or state.get("metadata_version", 1) not in (1, 2)
        ):
            raise RecoveryError("unsupported_state_version")
        self.state = (
            state
            if state is not None
            else {
                "version": 1,
                "files": {},
                "threads": {},
                "jobs": {},
                "seen": {},
                "health": {},
                "metadata_version": 2,
            }
        )
        self._migrate_metadata()
        if state is not None:
            # A previous process may have died between persisting intent and
            # recording ACK. That interval cannot be retried safely.
            for thread in self.state["threads"].values():
                dispatch = thread.get("last_dispatch", {})
                if dispatch.get("result") == "intent_persisted":
                    dispatch["result"] = "outcome_unknown"
                    thread["stopped_reason"] = "dispatch_outcome_unknown"
                    thread.setdefault(
                        "awaiting_start",
                        {
                            "failed_turn_id": dispatch["failed_turn_id"],
                            "deadline": dispatch["at"]
                            + self.cfg.get("start_confirmation_seconds", 30),
                        },
                    )
            self.state["operation"] = None

    def _migrate_metadata(self):
        if self.state.get("metadata_version") == 2:
            return
        observed = {}
        root = Path(self.cfg["sessions_root"])
        for name, cursor in self.state["files"].items():
            path = Path(name)
            tid = thread_id(path)
            if tid not in self.state["threads"] or not path.is_relative_to(root):
                continue
            try:
                st = path.stat()
                if st.st_ino != cursor["inode"] or st.st_size < cursor["offset"]:
                    continue
                records, _ = read_records(path, max_offset=cursor["offset"])
                observed.setdefault(tid, []).extend(records)
            except FileNotFoundError:
                continue
        for tid, records in observed.items():
            records.sort(key=lambda e: event_timestamp(e) or float("-inf"))
            old = self.state["threads"][tid]
            latest = fold_lifecycle(records)
            old.update(
                kind=latest["kind"],
                turn_id=latest["turn_id"],
                goal_status=latest["goal_status"],
                goal_revision=latest["goal_revision"],
            )
            times = [
                event_timestamp(e) for e in records if event_timestamp(e) is not None
            ]
            if times:
                old["last_event_time"] = max(times)
            old["recent_event_keys"] = [event_key(e) for e in records[-64:]]
        # Legacy pending jobs have no Goal revision snapshot. Do not reinterpret
        # an unverified old intent as permission to restart under a changed Goal.
        for tid, job in list(self.state["jobs"].items()):
            if "goal_revision" not in job:
                self.state["jobs"].pop(tid)
                self.state["threads"].setdefault(tid, {})[
                    "stopped_reason"
                ] = "state_upgrade_pending_unverifiable"
        self.state["metadata_version"] = 2

    def recoverable(self, event):
        return bool(
            event.get("recoverable")
            and (
                event.get("failure_kind") != "bad_request"
                or self.cfg.get("recover_bad_requests", True)
            )
        )

    def retry_limit(self, thread, failure_kind):
        if thread.get("attempts", 0) >= self.cfg["max_attempts"]:
            return True
        return failure_kind == "bad_request" and thread.get(
            "bad_request_attempts", 0
        ) >= self.cfg.get("bad_request_max_attempts", 3)

    def retry_delay(self, thread, failure_kind):
        if failure_kind == "bad_request":
            delays = self.cfg.get("bad_request_delays_seconds", [2, 5, 15])
            count = thread.get("bad_request_attempts", 0)
        else:
            delays = self.cfg["delays_seconds"]
            count = thread.get("attempts", 0)
        return delays[min(count, len(delays) - 1)]

    def event(self, tid, path, event):
        now = self.clock()
        timestamp = event_timestamp(event)
        if event.get("recoverable") and timestamp is None:
            return
        if timestamp is not None and timestamp < self.state.get(
            "monitor_started_at", 0
        ):
            return
        old = self.state["threads"].get(tid, {})
        kind, turn = event["kind"], event["turn_id"]
        if timestamp is not None and timestamp < old.get(
            "last_event_time", float("-inf")
        ):
            return
        fingerprint = event_key(event)
        recent = old.get("recent_event_keys", [])
        if fingerprint in recent:
            return
        old["recent_event_keys"] = (recent + [fingerprint])[-64:]
        if timestamp is not None:
            old["last_event_time"] = timestamp
        old.update({"path": str(path), "updated": now})
        self.state["threads"][tid] = old
        if kind == "thread_goal_updated":
            changed = goal_changed(
                old.get("goal_status"), old.get("goal_revision"), event
            )
            old.update(
                goal_status=goal_status(event), goal_revision=event.get("goal_revision")
            )
            if changed:
                job = self.state["jobs"].pop(tid, None)
                if job:
                    old["cancelled_failure_turn_id"] = job["turn_id"]
                    old["last_recovery_decision"] = {
                        "at": now,
                        "reason": "goal_updated_after_failure",
                    }
            return
        if kind == "native_retry":
            if turn is None or old.get("turn_id") in (None, turn):
                old.update(kind="task_started", turn_id=turn or old.get("turn_id"))
                self.state["jobs"].pop(tid, None)
            return
        if kind == "user_message":
            if (
                old.get("kind") == "task_started"
                and not old.get("recovery_input_seen", False)
                and old.get("active_recovery_turn_id") == old.get("turn_id")
                and old.get("active_recovery_id")
                == old.get("last_dispatch", {}).get("recovery_id")
                and own_recovery_message(
                    event, old.get("turn_id"), old.get("active_recovery_id")
                )
            ):
                old["recovery_input_seen"] = True
                return
            old["cancelled_failure_turn_id"] = old.get("turn_id")
            self.state["jobs"].pop(tid, None)
            old.pop("awaiting_start", None)
            old.update(
                kind=kind, turn_id=None, attempts=0, bad_request_attempts=0, outage_at=0
            )
            return
        if kind == "task_started":
            delivery = old.get("awaiting_start")
            identifier = recovery_id_from_trigger(event.get("turn_trigger"))
            same_recovery = (
                identifier is not None
                and identifier == old.get("active_recovery_id")
                and identifier == old.get("last_dispatch", {}).get("recovery_id")
                and turn == old.get("active_recovery_turn_id")
            )
            if same_recovery:
                return
            old.pop("active_recovery_id", None)
            old.pop("active_recovery_turn_id", None)
            old.pop("recovery_input_seen", None)
            if event.get("turn_trigger") in USER_REQUEST_TRIGGERS:
                old.update(attempts=0, bad_request_attempts=0, outage_at=0)
                old.pop("awaiting_start", None)
                if delivery:
                    old["last_dispatch"]["result"] = "superseded_by_human_request"
            elif delivery and turn != delivery["failed_turn_id"]:
                own_start = identifier is not None and identifier == old[
                    "last_dispatch"
                ].get("recovery_id")
                old["last_dispatch"].update(
                    result=(
                        "new_turn_observed" if own_start else "superseded_by_other_turn"
                    ),
                    resumed_turn_id=turn,
                    observed_at=now,
                )
                if own_start:
                    old.update(
                        active_recovery_id=identifier,
                        active_recovery_turn_id=turn,
                        recovery_input_seen=False,
                    )
                old.pop("awaiting_start", None)
                old.pop("stopped_reason", None)
            if old.get("cancelled_failure_turn_id") != turn:
                old.pop("cancelled_failure_turn_id", None)
            old.update(kind=kind, turn_id=turn)
            self.state["jobs"].pop(tid, None)
            return
        if kind == "turn_aborted":
            if turn is not None and old.get("turn_id") not in (None, turn):
                return
            old["cancelled_failure_turn_id"] = turn or old.get("turn_id")
            old.update(
                kind=kind, turn_id=turn, attempts=0, bad_request_attempts=0, outage_at=0
            )
            old.pop("awaiting_start", None)
            self.state["jobs"].pop(tid, None)
            return
        latest = None
        if self.recoverable(event):
            latest = latest_thread_lifecycle(self.cfg["sessions_root"], tid, path)
            if latest.get("kind") != "task_complete" or latest.get("turn_id") != turn:
                return
            old.update(
                goal_status=latest["goal_status"], goal_revision=latest["goal_revision"]
            )
        elif old.get("turn_id") not in (None, turn):
            return
        old.update(kind=kind, turn_id=turn)
        if not self.recoverable(event):
            self.state["jobs"].pop(tid, None)
            old.update(attempts=0, bad_request_attempts=0, outage_at=0)
            return
        if latest.get("goal_changed_after_terminal"):
            self.state["jobs"].pop(tid, None)
            old["cancelled_failure_turn_id"] = turn
            old["last_recovery_decision"] = {
                "at": now,
                "turn_id": turn,
                "reason": "goal_updated_after_failure",
            }
            return
        if not goal_allows_retry(self.cfg, latest.get("retry_goal_status")):
            old["last_recovery_decision"] = {
                "at": now,
                "turn_id": turn,
                "reason": "goal_not_retryable",
                "goal_status": old.get("goal_status"),
            }
            return
        if tid in self.cfg["exclude_thread_ids"] or not turn:
            old["last_recovery_decision"] = {
                "at": now,
                "turn_id": turn,
                "reason": "excluded_or_missing_turn",
            }
            return
        if old.get("cancelled_failure_turn_id") == turn:
            return
        existing = self.state["jobs"].get(tid)
        if existing and existing["turn_id"] == turn:
            return  # A duplicate terminal notification must not restart backoff.
        key = tid + ":" + turn
        if key in self.state["seen"]:
            return
        failure_kind = event["failure_kind"]
        outage = old.get("outage_at") or now
        if (
            self.retry_limit(old, failure_kind)
            or now - outage >= self.cfg["max_outage_seconds"]
        ):
            old["stopped_reason"] = "retry_limit"
            return
        self.state["jobs"][tid] = {
            "thread_id": tid,
            "turn_id": turn,
            "path": str(path),
            "due": now + self.retry_delay(old, failure_kind),
            "failure_kind": failure_kind,
            "outage_at": outage,
            "goal_revision": latest["goal_revision"],
        }
        old["last_recovery_decision"] = {
            "at": now,
            "turn_id": turn,
            "reason": "queued",
            "goal_status": old.get("goal_status"),
        }

    def request_retry(self, request):
        """Accept one explicit request for an unsent failure; automatic history stays inert."""
        tid, turn = request.get("thread_id"), request.get("turn_id")
        if any(
            not isinstance(value, str) or not THREAD_RE.fullmatch(value)
            for value in (tid, turn)
        ):
            return "invalid_thread_or_turn_id"
        now = self.clock()
        issued = request.get("requested_at")
        if (
            not isinstance(issued, (int, float))
            or isinstance(issued, bool)
            or not 0 <= now - issued <= 60
        ):
            return "request_expired_or_invalid"
        if not self.cfg["enabled"] or request.get("enabled_epoch") != self.cfg.get(
            "enabled_epoch"
        ):
            return "disabled_or_epoch_changed"
        old = self.state["threads"].get(tid)
        if not old or not old.get("path"):
            return "thread_not_monitored"
        previous = old.get("manual_retry", {})
        if previous.get("request_id") == request["request_id"]:
            return previous["result"]
        if previous.get("failed_turn_id") == turn:
            return "manual_retry_already_requested"
        if tid in self.cfg["exclude_thread_ids"]:
            return "excluded_thread"
        if tid in self.state["jobs"]:
            return "already_pending"
        if tid + ":" + turn in self.state["seen"] or old.get("awaiting_start"):
            return "already_dispatched_or_uncertain"
        latest = latest_thread_lifecycle(self.cfg["sessions_root"], tid, old["path"])
        if (
            latest.get("kind") != "task_complete"
            or latest.get("turn_id") != turn
            or not self.recoverable(latest)
        ):
            return "not_current_capacity_failure"
        failure_kind = latest["failure_kind"]
        if self.retry_limit(old, failure_kind):
            return "retry_limit"
        if not goal_allows_retry(self.cfg, latest.get("retry_goal_status")):
            return "goal_not_retryable"
        if latest.get("goal_changed_after_terminal"):
            return "goal_updated_after_failure"
        old["manual_retry"] = {
            "request_id": request["request_id"],
            "failed_turn_id": turn,
            "requested_at": issued,
            "accepted_at": now,
            "result": "accepted",
        }
        old.pop("stopped_reason", None)
        self.state["jobs"][tid] = {
            "thread_id": tid,
            "turn_id": turn,
            "path": old["path"],
            "due": now + self.retry_delay(old, failure_kind),
            "failure_kind": failure_kind,
            "outage_at": now,
            "goal_revision": latest.get("goal_revision"),
        }
        old["last_recovery_decision"] = {
            "at": now,
            "turn_id": turn,
            "reason": "explicit_retry_queued",
        }
        return "accepted"

    def scan(self):
        root = Path(self.cfg["sessions_root"])
        first_scan = "monitor_started_at" not in self.state
        if first_scan:
            self.state["monitor_started_at"] = self.clock()
        baseline_unseen = first_scan or self.state.get("file_discovery_version") != 2
        # Partitions encode thread creation, not recent activity. Old threads
        # keep appending in their original partition. Enumerate names/stat only
        # across the live sessions root; archived_sessions is a separate root.
        live_paths = set()
        pending_events = []
        for path in root.glob("*/*/*/*.jsonl"):
            tid = thread_id(path)
            if not tid or tid in self.cfg["exclude_thread_ids"]:
                continue
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            key = str(path)
            live_paths.add(key)
            cursor = self.state["files"].get(key)
            if cursor is None:
                # Expanding discovery on upgrade must not replay missed errors.
                offset = (
                    st.st_size
                    if baseline_unseen or st.st_mtime < self.state["monitor_started_at"]
                    else 0
                )
                cursor = {"offset": offset, "inode": st.st_ino}
                self.state["files"][key] = cursor
            if cursor["inode"] != st.st_ino or st.st_size < cursor["offset"]:
                cursor = {"offset": 0, "inode": st.st_ino}
                self.state["files"][key] = cursor
            if st.st_size == cursor["offset"]:
                continue
            try:
                events, offset = read_records(path, cursor["offset"])
            except FileNotFoundError:
                continue
            cursor["offset"] = offset
            for event in events:
                pending_events.append((tid, path, event))
        pending_events.sort(key=lambda item: event_timestamp(item[2]) or float("-inf"))
        for tid, path, event in pending_events:
            self.event(tid, path, event)
        self.state["file_discovery_version"] = 2
        cutoff = self.clock() - self.cfg["retention_days"] * 86400
        self.state["seen"] = {
            k: t for k, t in self.state["seen"].items() if t >= cutoff
        }
        self.state["threads"] = {
            k: v for k, v in self.state["threads"].items() if v["updated"] >= cutoff
        }
        self.state["files"] = {
            k: v for k, v in self.state["files"].items() if k in live_paths
        }

    def check_delivery(self):
        for thread in self.state["threads"].values():
            pending = thread.get("awaiting_start")
            if (
                pending
                and not pending.get("timed_out")
                and self.clock() >= pending["deadline"]
            ):
                pending["timed_out"] = True
                thread["last_dispatch"]["result"] = "start_not_observed"
                thread["stopped_reason"] = "start_not_observed"

    def dispatch(
        self,
        tid,
        persist,
        ipc_factory=DesktopIPC,
        allow_success=False,
        may_dispatch=lambda: True,
    ):
        job = self.state["jobs"][tid]
        expected = job["turn_id"]
        now = self.clock()

        def limit_reason():
            if tid in self.cfg["exclude_thread_ids"]:
                return "excluded_thread"
            if self.clock() - job["outage_at"] >= self.cfg["max_outage_seconds"]:
                return "outage_timeout"
            if self.retry_limit(self.state["threads"][tid], job.get("failure_kind")):
                return "retry_limit"
            if tid + ":" + expected in self.state["seen"]:
                return "already_dispatched"
            return None

        def stop(reason):
            self.state["jobs"].pop(tid, None)
            self.state["threads"][tid]["stopped_reason"] = reason
            persist()
            return reason

        reason = limit_reason()
        if reason:
            return stop(reason)

        def eligible():
            latest = latest_thread_lifecycle(
                self.cfg["sessions_root"], tid, job["path"]
            )
            return (
                latest.get("kind") == "task_complete"
                and latest.get("turn_id") == expected
                and (allow_success or self.recoverable(latest))
                and (
                    job.get("failure_kind") is None
                    or latest.get("failure_kind") == job["failure_kind"]
                )
                and goal_allows_retry(self.cfg, latest.get("retry_goal_status"))
                and latest.get("goal_revision") == job.get("goal_revision")
                and not latest.get("goal_changed_after_terminal")
            )

        if not eligible():
            self.state["jobs"].pop(tid, None)
            self.state["threads"][tid]["last_recovery_decision"] = {
                "at": now,
                "turn_id": expected,
                "reason": "superseded_active_or_goal_stopped",
            }
            self.state["threads"][tid]["cancelled_failure_turn_id"] = expected
            persist()
            return "superseded_or_active"
        ipc = None

        def stage(name):
            self.state["operation"] = {
                "thread_id": tid,
                "phase": name,
                "since": self.clock(),
            }
            persist()

        def await_start(result):
            thread = self.state["threads"][tid]
            thread["last_dispatch"].update(result=result)
            thread["awaiting_start"] = {
                "failed_turn_id": expected,
                "deadline": self.clock()
                + self.cfg.get("start_confirmation_seconds", 30),
            }

        def cancel():
            self.state["jobs"].pop(tid, None)
            self.state["threads"][tid]["cancelled_failure_turn_id"] = expected
            persist()
            return "superseded_or_active"

        try:
            stage("discover_owner")
            ipc = ipc_factory(self.cfg["socket_path"])
            try:
                owner = ipc.owner(tid)
            except RecoveryError as exc:
                if (
                    not self.cfg["recover_missing_owners"]
                    or str(exc) not in OWNER_UNAVAILABLE_ERRORS
                ):
                    raise
                if not may_dispatch() or not eligible():
                    return cancel()
                if not self.cfg["recover_missing_owners"]:
                    raise
                reason = limit_reason()
                if reason:
                    return stop(reason)
                thread = self.state["threads"][tid]
                prior = thread.get("owner_activation", {})
                if prior.get("failed_turn_id") == expected:
                    raise
                # A URL navigation is separate from a model request. Claim it
                # durably before launching; never reopen on restart/timeout.
                thread["owner_activation"] = {
                    "failed_turn_id": expected,
                    "at": self.clock(),
                    "result": "intent_persisted",
                    "attempts": 1,
                }
                stage("activate_owner")
                try:
                    activate_desktop_thread(tid)
                except (OSError, RecoveryError):
                    thread["owner_activation"]["result"] = "activation_failed"
                    raise
                thread["owner_activation"]["result"] = "requested"
                job["due"] = self.clock() + self.cfg["poll_seconds"]
                thread["last_recovery_decision"] = {
                    "at": self.clock(),
                    "turn_id": expected,
                    "reason": "owner_activation_requested",
                }
                return "owner_activation_requested"
            activation = self.state["threads"][tid].get("owner_activation", {})
            if activation.get("failed_turn_id") == expected:
                activation.update(result="owner_observed", observed_at=self.clock())
            stage("read_settings")
            settings = ipc.settings(tid, owner)
            if settings.get("mode") not in (None, "durable", "default"):
                raise RecoveryError("unsupported_thread_mode")
            if not may_dispatch() or not eligible():
                return cancel()
            reason = limit_reason()
            if reason:
                return stop(reason)
            # Persist intent BEFORE the single mutating request. Crash/timeout never replays it.
            key = tid + ":" + expected
            self.state["seen"][key] = now
            t = self.state["threads"][tid]
            t.update(attempts=t.get("attempts", 0) + 1, outage_at=job["outage_at"])
            if job.get("failure_kind") == "bad_request":
                t["bad_request_attempts"] = t.get("bad_request_attempts", 0) + 1
            t["last_dispatch"] = {
                "failed_turn_id": expected,
                "recovery_id": str(uuid.uuid4()),
                "at": self.clock(),
                "result": "intent_persisted",
                "failure_kind": job.get("failure_kind", "capacity"),
            }
            self.state["operation"] = {
                "thread_id": tid,
                "phase": "start_turn",
                "since": self.clock(),
            }
            self.state["jobs"].pop(tid, None)
            persist()
            ipc.continue_thread(
                tid,
                owner,
                settings["model"],
                settings.get("reasoningEffort"),
                recovery_id=t["last_dispatch"]["recovery_id"],
            )
            await_start("acknowledged")
            persist()
            return "acknowledged"
        except AmbiguousDispatch:
            self.state["threads"][tid]["stopped_reason"] = "dispatch_outcome_unknown"
            await_start("outcome_unknown")
            persist()
            return "dispatch_outcome_unknown"
        except (OSError, RecoveryError):
            # Read-only IPC failure consumes no provider attempt. A bounded outage
            # deadline still applies, and active/new input is checked on each pass.
            if tid in self.state["jobs"]:
                self.state["jobs"][tid]["due"] = self.clock() + 30
                self.state["threads"][tid]["last_recovery_decision"] = {
                    "at": self.clock(),
                    "turn_id": expected,
                    "reason": "desktop_route_unavailable",
                }
            else:
                self.state["threads"][tid][
                    "stopped_reason"
                ] = "dispatch_outcome_unknown"
                await_start("outcome_unknown")
            persist()
            return "desktop_route_unavailable"
        finally:
            if ipc:
                ipc.close()
            self.state["operation"] = None
            persist()


def daemon(data, once=False):
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (data / "daemon.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        engine = Engine(config(data), load_json(data / "state.json", None))

        def persist():
            atomic_json(data / "state.json", engine.state)

        while True:
            engine.cfg = config(data)
            epoch = engine.cfg.get("enabled_epoch")
            if engine.state.get("enabled_epoch") != epoch:
                # Re-enabling begins at current EOF, never replays failures while paused.
                engine.state.update(enabled_epoch=epoch, files={}, threads={}, jobs={})
                engine.state.pop("monitor_started_at", None)
            if engine.cfg["enabled"]:
                engine.scan()
                persist()
                process_requests(data, engine, persist)
                work_started = time.monotonic()
                for tid, job in sorted(
                    list(engine.state["jobs"].items()), key=lambda item: item[1]["due"]
                ):
                    if job["due"] <= time.time() and tid in engine.state["jobs"]:

                        def may_dispatch():
                            engine.cfg = config(data)
                            return (
                                engine.cfg["enabled"]
                                and engine.cfg.get("enabled_epoch") == epoch
                            )

                        engine.dispatch(
                            tid,
                            persist,
                            may_dispatch=may_dispatch,
                        )
                        if time.monotonic() - work_started >= 1:
                            break  # Poll/cancel/heartbeat again after one slow IPC lookup.
            else:
                engine.state["jobs"].clear()
            engine.check_delivery()
            engine.state["health"] = {
                "pid": os.getpid(),
                "heartbeat": time.time(),
                "enabled": engine.cfg["enabled"],
                "poll_seconds": engine.cfg["poll_seconds"],
                "delays_seconds": engine.cfg["delays_seconds"],
            }
            persist()
            if once:
                return
            time.sleep(engine.cfg["poll_seconds"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    daemon(args.data_dir, args.once)


if __name__ == "__main__":
    main()
