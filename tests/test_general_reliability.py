import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import retry_watchdog as w
from test_retry_watchdog import FakeIPC, TID, TURN


class GeneralReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ("rollout-" + TID + ".jsonl")
        self.path.touch()
        self.cfg = dict(w.DEFAULT, sessions_root=self.tmp.name)
        self.clock = [1000.0]
        self.e = w.Engine(self.cfg, clock=lambda: self.clock[0])
        FakeIPC.count = 0
        FakeIPC.fail = False
        FakeIPC.active_before_dispatch = None

    def emit(self, kind, turn=TURN, stamp="2026-10-30T03:00:00Z", **fields):
        raw = (
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": stamp,
                    "payload": {"type": kind, "turn_id": turn, **fields},
                }
            )
            + "\n"
        ).encode()
        with self.path.open("ab") as stream:
            stream.write(raw)
        self.e.event(TID, self.path, w.metadata(raw))

    def failure(self, turn=TURN):
        self.emit("task_started", turn)
        self.emit(
            "task_complete", turn, error={"codex_error_info": "server_overloaded"}
        )

    def test_message_only_error_is_not_capacity(self):
        message = "Selected model is at capacity. Please try a different model."
        self.assertFalse(w.classify_capacity(message))
        self.assertFalse(w.classify_capacity({"message": message}))

    def test_duplicate_failure_does_not_postpone_due(self):
        self.failure()
        due = self.e.state["jobs"][TID]["due"]
        self.clock[0] += 0.8
        self.emit(
            "task_complete",
            stamp="2026-10-30T03:00:00.800Z",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertEqual(self.e.state["jobs"][TID]["due"], due)

    def test_cancellation_is_not_undone_by_late_duplicate_failure(self):
        for cancel in ("user_message", "turn_aborted"):
            with self.subTest(cancel=cancel):
                self.path.write_bytes(b"")
                self.e = w.Engine(self.cfg, clock=lambda: self.clock[0])
                self.failure()
                self.emit(cancel, stamp="2026-10-30T03:00:01Z")
                self.emit(
                    "task_complete",
                    stamp="2026-10-30T03:00:02Z",
                    error={"codex_error_info": "server_overloaded"},
                )
                self.assertNotIn(TID, self.e.state["jobs"])
                self.assertNotEqual(
                    w.latest_lifecycle(self.path)["kind"], "task_complete"
                )

    def test_pre_send_cancellation_with_unscanned_duplicate_failure_blocks_dispatch(
        self,
    ):
        self.failure()
        for kind, stamp in [("user_message", "01"), ("task_complete", "02")]:
            event = {
                "type": "event_msg",
                "timestamp": f"2026-10-30T03:00:{stamp}Z",
                "payload": {
                    "type": kind,
                    "turn_id": TURN,
                    "error": (
                        {"codex_error_info": "server_overloaded"}
                        if kind == "task_complete"
                        else None
                    ),
                },
            }
            with self.path.open("a") as stream:
                stream.write(json.dumps(event) + "\n")
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC), "superseded_or_active"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_late_old_abort_cannot_cancel_current_failure(self):
        self.failure()
        self.emit("turn_aborted", turn="older-turn", stamp="2026-10-30T02:59:59Z")
        self.assertIn(TID, self.e.state["jobs"])
        self.assertEqual(self.e.state["threads"][TID]["turn_id"], TURN)

    def test_late_old_completion_in_real_log_cannot_replace_active_turn(self):
        self.emit("task_started", turn="current")
        self.emit(
            "task_complete",
            turn="older",
            stamp="2026-10-30T03:00:01Z",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertFalse(self.e.state["jobs"])
        self.assertEqual(self.e.state["threads"][TID]["turn_id"], "current")

    def test_duplicate_failure_after_goal_edit_stays_cancelled(self):
        self.emit("thread_goal_updated", goal={"status": "active", "objective": "old"})
        self.failure()
        self.emit(
            "thread_goal_updated",
            goal={"status": "active", "objective": "new"},
            stamp="2026-10-30T03:00:01Z",
        )
        self.emit(
            "task_complete",
            stamp="2026-10-30T03:00:02Z",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_composer_turn_starts_a_fresh_retry_budget(self):
        self.failure()
        self.e.state["threads"][TID].update(attempts=5, outage_at=100)
        self.emit(
            "task_started",
            turn="manual",
            stamp="2026-10-30T03:00:01Z",
            turn_attribution={"turn_trigger": "composer"},
        )
        self.emit(
            "task_complete",
            turn="manual",
            stamp="2026-10-30T03:00:02Z",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertIn(TID, self.e.state["jobs"])
        self.assertEqual(self.e.state["threads"][TID]["attempts"], 0)

    def test_new_human_request_with_paused_goal_can_retry_without_resuming_goal(self):
        self.emit(
            "thread_goal_updated", goal={"status": "paused", "objective": "unchanged"}
        )
        self.emit(
            "task_started",
            stamp="2026-10-30T03:00:01Z",
            turn_attribution={"turn_trigger": "composer"},
        )
        self.emit(
            "task_complete",
            stamp="2026-10-30T03:00:02Z",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertIn(TID, self.e.state["jobs"])
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "acknowledged")
        self.assertEqual(self.e.state["threads"][TID]["goal_status"], "paused")

    def test_pause_during_composer_turn_still_blocks_retry(self):
        self.emit("thread_goal_updated", goal={"status": "active"})
        self.emit(
            "task_started",
            stamp="2026-10-30T03:00:01Z",
            turn_attribution={"turn_trigger": "composer"},
        )
        self.emit(
            "thread_goal_updated",
            goal={"status": "paused"},
            stamp="2026-10-30T03:00:02Z",
        )
        self.emit(
            "task_complete",
            stamp="2026-10-30T03:00:03Z",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_ordinary_retry_chain_with_old_paused_goal_keeps_its_budget(self):
        self.emit("thread_goal_updated", goal={"status": "paused"})
        self.emit("task_started", turn_attribution={"turn_trigger": "composer"})
        self.emit("task_complete", error={"codex_error_info": "server_overloaded"})
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "acknowledged")
        self.emit("task_started", turn="retry", stamp="2026-10-30T03:00:01Z")
        self.emit(
            "task_complete",
            turn="retry",
            stamp="2026-10-30T03:00:02Z",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertIn(TID, self.e.state["jobs"])
        self.assertEqual(self.e.state["jobs"][TID]["due"], 1002)
        self.assertEqual(self.e.state["threads"][TID]["attempts"], 1)

    def test_removed_goal_allows_future_ordinary_turn(self):
        self.emit("thread_goal_updated", goal={"status": "paused"})
        self.emit("thread_goal_updated", goal=None, stamp="2026-10-30T03:00:01Z")
        self.emit("task_started", stamp="2026-10-30T03:00:02Z")
        self.emit(
            "task_complete",
            stamp="2026-10-30T03:00:03Z",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertIn(TID, self.e.state["jobs"])
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "acknowledged")

    def test_goal_usage_snapshot_does_not_cancel_retry(self):
        goal = {
            "status": "active",
            "objective": "test objective",
            "createdAt": 10,
            "updatedAt": 10,
            "tokensUsed": 0,
            "timeUsedSeconds": 0,
        }
        self.emit("thread_goal_updated", goal=goal)
        self.failure()
        self.emit(
            "thread_goal_updated",
            stamp="2026-10-30T03:00:01Z",
            goal=dict(goal, updatedAt=11, tokensUsed=100, timeUsedSeconds=1),
        )
        self.assertIn(TID, self.e.state["jobs"])

    def test_goal_objective_edit_does_cancel_retry(self):
        goal = {"status": "active", "objective": "first", "createdAt": 10}
        self.emit("thread_goal_updated", goal=goal)
        self.failure()
        self.emit(
            "thread_goal_updated",
            goal=dict(goal, objective="second"),
            stamp="2026-10-30T03:00:01Z",
        )
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_outage_deadline_rechecked_after_slow_lookup(self):
        self.failure()
        FakeIPC.active_before_dispatch = staticmethod(
            lambda: self.clock.__setitem__(0, 2000.0)
        )
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "outage_timeout")
        self.assertEqual(FakeIPC.count, 0)

    def test_lowered_attempt_limit_applies_to_queued_job(self):
        self.failure()
        self.e.state["threads"][TID]["attempts"] = 1
        self.cfg["max_attempts"] = 1
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "retry_limit")
        self.assertEqual(FakeIPC.count, 0)

    def test_new_exclusion_applies_to_existing_queue(self):
        self.failure()
        self.cfg["exclude_thread_ids"] = [TID]
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "excluded_thread")
        self.assertEqual(FakeIPC.count, 0)

    def test_acknowledged_send_is_confirmed_by_real_start_event(self):
        self.failure()
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "acknowledged")
        identifier = self.e.state["threads"][TID]["last_dispatch"]["recovery_id"]
        self.emit(
            "task_started",
            turn="resumed",
            stamp="2026-10-30T03:00:01Z",
            turn_attribution={"turn_trigger": "codex_auto_retry_" + identifier},
        )
        last = self.e.state["threads"][TID]["last_dispatch"]
        self.assertEqual(last.get("result"), "new_turn_observed")
        self.assertEqual(last.get("resumed_turn_id"), "resumed")

    def test_ack_without_start_is_reported_without_resend(self):
        self.failure()
        self.e.dispatch(TID, lambda: None, FakeIPC)
        self.clock[0] += 61
        self.e.check_delivery()
        self.assertEqual(
            self.e.state["threads"][TID]["last_dispatch"]["result"],
            "start_not_observed",
        )
        self.assertNotIn(TID, self.e.state["jobs"])
        self.assertEqual(FakeIPC.count, 1)

    def test_malformed_metadata_shapes_are_ignored(self):
        for payload in [None, [], "text", {"type": "task_complete", "turn_id": []}]:
            with self.subTest(payload=payload):
                raw = json.dumps({"type": "event_msg", "payload": payload}).encode()
                self.assertIsNone(w.metadata(raw))

    def test_malformed_goal_snapshot_blocks_pending_retry(self):
        self.failure()
        self.emit(
            "thread_goal_updated", goal="not-an-object", stamp="2026-10-30T03:00:01Z"
        )
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_enabled_setting_requires_boolean(self):
        p = Path(self.tmp.name) / "config.json"
        p.write_text(json.dumps({"enabled": "false"}))
        with self.assertRaises(w.RecoveryError):
            w.config(Path(self.tmp.name))

    def test_future_state_schema_is_not_silently_downgraded(self):
        state = json.loads(json.dumps(self.e.state))
        state["metadata_version"] = 99
        with self.assertRaises(w.RecoveryError):
            w.Engine(self.cfg, state)

    def test_inode_replacement_with_new_failure_is_not_missed(self):
        folder = Path(self.tmp.name) / "2026/10/30"
        folder.mkdir(parents=True)
        self.path = folder / self.path.name
        self.path.touch()
        self.e.scan()
        replacement = Path(self.tmp.name) / "replacement"
        replacement.write_text(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": "2026-10-30T03:00:01Z",
                    "payload": {"type": "task_started", "turn_id": TURN},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": "2026-10-30T03:00:02Z",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": TURN,
                        "error": {"codex_error_info": "server_overloaded"},
                    },
                }
            )
            + "\n"
        )
        replacement.replace(self.path)
        self.e.scan()
        self.assertIn(TID, self.e.state["jobs"])

    def test_restart_migration_reads_only_already_consumed_prefix(self):
        folder = Path(self.tmp.name) / "2026/10/30"
        folder.mkdir(parents=True)
        self.path = folder / self.path.name
        self.emit(
            "thread_goal_updated", goal={"status": "active", "objective": "unchanged"}
        )
        self.emit("task_started", stamp="2026-10-30T03:00:01Z")
        offset = self.path.stat().st_size
        legacy = {
            "version": 1,
            "files": {
                str(self.path): {"offset": offset, "inode": self.path.stat().st_ino}
            },
            "threads": {
                TID: {
                    "path": str(self.path),
                    "kind": "task_started",
                    "turn_id": TURN,
                    "updated": 1000,
                    "attempts": 2,
                    "outage_at": 999,
                    "goal_status": "active",
                }
            },
            "jobs": {},
            "seen": {},
            "health": {},
            "monitor_started_at": 1000,
            "file_discovery_version": 2,
        }
        self.emit(
            "task_complete",
            stamp="2026-10-30T03:00:02Z",
            error={"codex_error_info": "server_overloaded"},
        )
        restored = w.Engine(self.cfg, legacy, clock=lambda: self.clock[0])
        restored.scan()
        self.assertIn(TID, restored.state["jobs"])
        self.assertEqual(restored.state["jobs"][TID]["due"], 1004)
        self.assertEqual(restored.state["threads"][TID]["attempts"], 2)

    def test_existing_intent_prevents_dispatch_of_inconsistent_restored_job(self):
        self.failure()
        job = dict(self.e.state["jobs"][TID])
        self.e.dispatch(TID, lambda: None, FakeIPC)
        self.e.state["jobs"][TID] = job
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC), "already_dispatched"
        )
        self.assertEqual(FakeIPC.count, 1)

    def test_restart_after_persisted_send_intent_observes_without_resending(self):
        self.failure()
        self.e.dispatch(TID, lambda: None, FakeIPC)
        saved = json.loads(json.dumps(self.e.state))
        saved["threads"][TID].pop("awaiting_start")
        saved["threads"][TID]["last_dispatch"]["result"] = "intent_persisted"
        saved["operation"] = {"thread_id": TID, "phase": "start_turn", "since": 1000}
        restored = w.Engine(self.cfg, saved, clock=lambda: self.clock[0])
        self.assertIsNone(restored.state.get("operation"))
        thread = restored.state["threads"][TID]
        self.assertEqual(thread["last_dispatch"]["result"], "outcome_unknown")
        self.assertIn("awaiting_start", thread)
        self.assertFalse(restored.state["jobs"])

    def test_goal_edit_during_ipc_read_prevents_dispatch(self):
        self.emit("thread_goal_updated", goal={"status": "active", "objective": "old"})
        self.failure()

        def changed():
            raw = {
                "type": "event_msg",
                "timestamp": "2026-10-30T03:00:01Z",
                "payload": {
                    "type": "thread_goal_updated",
                    "goal": {"status": "active", "objective": "new"},
                },
            }
            with self.path.open("a") as f:
                f.write(json.dumps(raw) + "\n")

        FakeIPC.active_before_dispatch = staticmethod(changed)
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC), "superseded_or_active"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_slow_lookup_yields_to_scan_and_heartbeat_before_next_job(self):
        directory = Path(self.tmp.name) / "daemon"
        directory.mkdir()
        cfg = dict(self.cfg, enabled=True, enabled_epoch="epoch")
        w.atomic_json(directory / "config.json", cfg)
        state = json.loads(json.dumps(self.e.state))
        state["enabled_epoch"] = "epoch"
        state["jobs"] = {TID: {"due": 0}, "other": {"due": 0}}
        w.atomic_json(directory / "state.json", state)
        calls = []

        def dispatch(engine, tid, *args, **kwargs):
            calls.append(tid)
            engine.state["jobs"].pop(tid)

        with patch.object(w.Engine, "scan", lambda engine: None), patch.object(
            w.Engine, "dispatch", dispatch
        ), patch.object(w.time, "monotonic", side_effect=[0, 2]):
            w.daemon(directory, once=True)
        self.assertEqual(len(calls), 1)


class IPCReliabilityTests(unittest.TestCase):
    def make_client(self):
        client = object.__new__(w.DesktopIPC)
        client.sock, server = socket.socketpair()
        client.sock.settimeout(0.2)
        client.client = "test"
        client.timeout = 0.04
        self.addCleanup(client.close)
        return client, server

    @staticmethod
    def read_request(server):
        def exact(count):
            data = b""
            while len(data) < count:
                chunk = server.recv(count - len(data))
                if not chunk:
                    raise EOFError("client disconnected")
                data += chunk
            return data

        return json.loads(exact(struct.unpack("<I", exact(4))[0]))

    def test_entire_frame_has_one_absolute_deadline(self):
        client, server = self.make_client()

        def respond():
            with server:
                request = self.read_request(server)
                body = json.dumps(
                    {
                        "type": "response",
                        "requestId": request["requestId"],
                        "resultType": "success",
                        "result": {},
                    }
                ).encode()
                server.sendall(struct.pack("<I", len(body)) + body[:2])
                time.sleep(0.08)
                try:
                    server.sendall(body[2:])
                except BrokenPipeError:
                    pass

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            with self.assertRaises(w.RecoveryError):
                client.request("read", 1, {})
        finally:
            client.close()
            thread.join(timeout=1)

    def test_disconnect_after_mutating_send_is_ambiguous(self):
        client, server = self.make_client()

        def disconnect():
            with server:
                self.read_request(server)

        thread = threading.Thread(target=disconnect)
        thread.start()
        try:
            with self.assertRaises(w.AmbiguousDispatch):
                client.request("write", 1, {}, mutating=True)
        finally:
            thread.join(timeout=1)

    def test_malformed_response_after_send_is_bounded_and_classified(self):
        for mutating, error in ((False, w.RecoveryError), (True, w.AmbiguousDispatch)):
            with self.subTest(mutating=mutating):
                client, server = self.make_client()

                def respond():
                    with server:
                        self.read_request(server)
                        body = b"[]"
                        server.sendall(struct.pack("<I", len(body)) + body)

                thread = threading.Thread(target=respond)
                thread.start()
                try:
                    with self.assertRaises(error):
                        client.request("method", 1, {}, mutating=mutating)
                finally:
                    thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
