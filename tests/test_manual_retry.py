import json
from pathlib import Path
import tempfile
import unittest

import retry_watchdog as w
from codex_retry.manual_retry import process_requests, submit_retry
from codex_retry.storage import atomic_json
from test_retry_watchdog import FakeIPC, TID, TURN


class ManualRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.sessions = self.data / "sessions"
        self.sessions.mkdir()
        self.path = self.sessions / ("rollout-" + TID + ".jsonl")
        self.path.touch()
        self.clock = [1000.0]
        self.cfg = dict(
            w.DEFAULT,
            enabled=True,
            enabled_epoch="test-epoch",
            sessions_root=str(self.sessions),
        )
        atomic_json(self.data / "config.json", self.cfg)
        self.engine = w.Engine(self.cfg, clock=lambda: self.clock[0])
        FakeIPC.count = 0
        FakeIPC.fail = False
        FakeIPC.active_before_dispatch = None
        self.emit("task_started")
        self.emit("task_complete", error={"codex_error_info": "server_overloaded"})
        self.engine.state["jobs"].clear()
        self.engine.state["threads"][TID]["stopped_reason"] = "outage_timeout"

    def emit(self, kind, **fields):
        self.clock[0] += 1
        raw = (
            json.dumps(
                {
                    "timestamp": w.dt.datetime.fromtimestamp(
                        self.clock[0], w.dt.timezone.utc
                    ).isoformat(),
                    "type": "event_msg",
                    "payload": {"type": kind, "turn_id": TURN, **fields},
                }
            )
            + "\n"
        ).encode()
        with self.path.open("ab") as stream:
            stream.write(raw)
        self.engine.event(TID, self.path, w.metadata(raw))

    def persist(self):
        atomic_json(self.data / "state.json", self.engine.state)

    def submit(self):
        return submit_retry(self.data, TID, TURN, clock=lambda: self.clock[0])

    def process(self, request):
        process_requests(self.data, self.engine, self.persist)
        return json.loads(
            (
                self.data / "requests" / (request["request_id"] + ".result.json")
            ).read_text()
        )["result"]

    def test_installed_daemon_path_validates_explicit_request_before_one_send(self):
        request = self.submit()
        self.assertEqual(request["result"], "submitted_not_dispatched")
        self.assertEqual(FakeIPC.count, 0)
        self.assertEqual(self.process(request), "accepted")
        self.assertEqual(self.engine.state["jobs"][TID]["outage_at"], self.clock[0])
        self.assertEqual(
            self.engine.dispatch(TID, self.persist, FakeIPC), "acknowledged"
        )
        self.assertEqual(FakeIPC.count, 1)
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_repeated_request_after_acceptance_does_not_requeue_or_reset_budget(self):
        request = self.submit()
        self.assertEqual(self.process(request), "accepted")
        outage = self.engine.state["jobs"][TID]["outage_at"]
        self.clock[0] += 10
        self.assertEqual(self.process(self.submit()), "manual_retry_already_requested")
        self.assertEqual(self.engine.state["jobs"][TID]["outage_at"], outage)
        self.engine.state["jobs"].clear()
        self.assertEqual(self.engine.request_retry(request), "accepted")
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_expired_or_paused_request_does_not_dispatch(self):
        request = self.submit()
        self.clock[0] += 61
        self.assertEqual(self.process(request), "request_expired_or_invalid")
        self.assertNotIn(TID, self.engine.state["jobs"])
        request = self.submit()
        self.engine.cfg["enabled_epoch"] = "new-epoch"
        self.assertEqual(self.process(request), "disabled_or_epoch_changed")

    def test_a_new_input_or_stopped_goal_prevents_explicit_retry(self):
        request = self.submit()
        self.emit("user_message")
        self.assertEqual(self.process(request), "not_current_capacity_failure")
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_seen_or_unknown_dispatch_is_never_replayed(self):
        for field in ("seen", "awaiting"):
            if field == "seen":
                self.engine.state["seen"][TID + ":" + TURN] = self.clock[0]
            else:
                self.engine.state["seen"].clear()
                self.engine.state["threads"][TID]["awaiting_start"] = {
                    "failed_turn_id": TURN
                }
            self.assertEqual(
                self.process(self.submit()), "already_dispatched_or_uncertain"
            )

    def test_stopped_goal_prevents_explicit_retry(self):
        self.emit("thread_goal_updated", goal={"status": "paused"})
        self.assertEqual(self.process(self.submit()), "goal_not_retryable")
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_active_goal_edit_after_expired_failure_is_not_new_retry_authority(self):
        self.emit(
            "thread_goal_updated",
            goal={"status": "active", "objective": "a different synthetic task"},
        )
        self.assertEqual(self.process(self.submit()), "goal_updated_after_failure")
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_goal_edit_then_deletion_cannot_hide_changed_authority(self):
        self.emit(
            "thread_goal_updated", goal={"status": "active", "objective": "synthetic"}
        )
        self.emit("thread_goal_updated", goal=None)
        self.assertNotEqual(self.process(self.submit()), "accepted")
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_restoring_goal_and_duplicate_terminal_do_not_revive_old_failure(self):
        self.path.write_bytes(b"")
        self.engine = w.Engine(self.cfg, clock=lambda: self.clock[0])
        goal = {"status": "active", "objective": "original synthetic task"}
        self.emit("thread_goal_updated", goal=goal)
        self.emit("task_started")
        self.emit("task_complete", error={"codex_error_info": "server_overloaded"})
        self.engine.state["jobs"].clear()
        self.emit(
            "thread_goal_updated", goal={"status": "active", "objective": "changed"}
        )
        self.emit("thread_goal_updated", goal=goal)
        self.emit("task_complete", error={"codex_error_info": "server_overloaded"})
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.assertEqual(self.process(self.submit()), "goal_updated_after_failure")
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_already_pending_failure_keeps_existing_deadline(self):
        self.engine.state["jobs"][TID] = {
            "turn_id": TURN,
            "outage_at": 1000,
            "due": 1001,
        }
        self.assertEqual(self.process(self.submit()), "already_pending")
        self.assertEqual(self.engine.state["jobs"][TID]["outage_at"], 1000)

    def test_disabled_submit_and_untrusted_ids_fail_before_writing(self):
        with self.assertRaises(w.RecoveryError):
            submit_retry(self.data, TID + "/../bad", TURN)
        atomic_json(self.data / "config.json", dict(self.cfg, enabled=False))
        with self.assertRaises(w.RecoveryError):
            self.submit()

    def test_duplicate_request_file_after_crash_does_not_restore_dispatched_job(self):
        request = self.submit()
        self.engine.request_retry(request)
        self.persist()
        self.engine.dispatch(TID, self.persist, FakeIPC)
        restored = w.Engine(
            self.cfg,
            json.loads((self.data / "state.json").read_text()),
            clock=lambda: self.clock[0],
        )
        self.engine = restored
        self.assertEqual(self.process(request), "accepted")
        self.assertNotIn(TID, restored.state["jobs"])
        self.assertEqual(FakeIPC.count, 1)
