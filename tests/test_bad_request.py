import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import retry_watchdog as w
from codex_retry.recovery_input import CONTINUATION_TEXT
from test_retry_watchdog import FakeIPC, TID, TURN


GENERIC_BAD_REQUEST = {
    "codex_error_info": "other",
    "message": '{"detail":"Bad Request"}',
}


class BadRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = (
            Path(self.tmp.name) / "2026/11/11" / ("rollout-" + TID + ".jsonl")
        )
        self.path.parent.mkdir(parents=True)
        self.path.touch()
        self.clock = [1000.0]
        self.engine = w.Engine(
            dict(w.DEFAULT, sessions_root=self.tmp.name), clock=lambda: self.clock[0]
        )
        self.engine.scan()
        FakeIPC.count, FakeIPC.fail, FakeIPC.active_before_dispatch = 0, False, None

    def emit(self, kind, turn=TURN, **extra):
        self.clock[0] += 1
        record = {
            "type": "event_msg",
            "timestamp": w.dt.datetime.fromtimestamp(
                self.clock[0], w.dt.timezone.utc
            ).isoformat(),
            "payload": {"type": kind, "turn_id": turn, **extra},
        }
        with self.path.open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        self.engine.scan()

    def failure(self, error=GENERIC_BAD_REQUEST, **extra):
        self.emit("task_started")
        self.emit("task_complete", error=error, **extra)

    def own_start(self, turn):
        identifier = self.engine.state["threads"][TID]["last_dispatch"]["recovery_id"]
        self.emit(
            "task_started",
            turn,
            turn_attribution={"turn_trigger": "codex_auto_retry_" + identifier},
        )
        self.emit(
            "user_message",
            turn,
            message=CONTINUATION_TEXT,
        )

    def test_generic_bad_request_is_detected_and_continues_the_same_thread(self):
        self.failure()
        self.assertIn(TID, self.engine.state["jobs"])
        self.assertEqual(self.engine.state["jobs"][TID]["due"], self.clock[0] + 2)
        self.assertEqual(
            self.engine.dispatch(TID, lambda: None, FakeIPC), "acknowledged"
        )
        self.assertEqual(FakeIPC.count, 1)
        self.own_start("automatic")
        self.assertEqual(
            self.engine.state["threads"][TID]["last_dispatch"]["result"],
            "new_turn_observed",
        )

    def test_three_retry_limit_and_backoff_survive_restart(self):
        self.emit("task_started")
        for index in range(4):
            turn = TURN if index == 0 else "automatic-" + str(index)
            self.emit("task_complete", turn, error=GENERIC_BAD_REQUEST)
            if index == 3:
                self.assertNotIn(TID, self.engine.state["jobs"])
                break
            self.assertEqual(
                self.engine.state["jobs"][TID]["due"],
                self.clock[0] + [2, 5, 15][index],
            )
            self.engine.dispatch(TID, lambda: None, FakeIPC)
            self.engine = w.Engine(
                self.engine.cfg,
                json.loads(json.dumps(self.engine.state)),
                clock=lambda: self.clock[0],
            )
            self.own_start("automatic-" + str(index + 1))
        self.assertEqual(FakeIPC.count, 3)
        self.assertEqual(self.engine.state["threads"][TID]["bad_request_attempts"], 3)

    def test_detailed_or_nonterminal_errors_do_not_queue(self):
        cases = [
            {"codex_error_info": "badRequest", "message": "Invalid tool schema"},
            {"codex_error_info": "other", "message": 'prefix {"detail":"Bad Request"}'},
            {"codex_error_info": "unauthorized", "message": "Bad Request"},
            {"codex_error_info": {"httpConnectionFailed": {"httpStatusCode": 400}}},
            dict(GENERIC_BAD_REQUEST, additionalDetails="invalid parameter"),
            dict(GENERIC_BAD_REQUEST, willRetry=True),
            {"codex_error_info": {"badRequest": {"detail": "Invalid tool schema"}}},
            {"codex_error_info": {"badRequest": {"code": "unauthorized"}}},
            {
                "codex_error_info": {"badRequest": {"detail": "Bad Request"}},
                "message": "Invalid tool schema",
            },
        ]
        for index, error in enumerate(cases):
            with self.subTest(error=error):
                self.emit("task_started", "rejected-" + str(index))
                self.emit("task_complete", "rejected-" + str(index), error=error)
                self.assertNotIn(TID, self.engine.state["jobs"])
        self.failure(willRetry=True)
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.failure(status="interrupted")
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_structured_bad_request_and_opt_out(self):
        self.failure({"codexErrorInfo": "badRequest"})
        self.assertIn(TID, self.engine.state["jobs"])
        self.engine.cfg["recover_bad_requests"] = False
        self.assertEqual(
            self.engine.dispatch(TID, lambda: None, FakeIPC), "superseded_or_active"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_human_stop_and_uncertain_send_do_not_replay(self):
        self.failure()
        self.emit("turn_aborted")
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.emit(
            "task_started", "new-request", turn_attribution={"turn_trigger": "composer"}
        )
        self.emit("task_complete", "new-request", error=GENERIC_BAD_REQUEST)
        FakeIPC.fail = True
        self.assertEqual(
            self.engine.dispatch(TID, lambda: None, FakeIPC), "dispatch_outcome_unknown"
        )
        self.engine = w.Engine(
            self.engine.cfg,
            json.loads(json.dumps(self.engine.state)),
            clock=lambda: self.clock[0],
        )
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.assertEqual(FakeIPC.count, 1)

    def test_bad_request_raw_text_is_not_retained_or_taken_from_tool_output(self):
        self.failure()
        events, _ = w.read_records(self.path)
        self.assertEqual(events[-1]["failure_kind"], "bad_request")
        self.assertNotIn("Bad Request", json.dumps(events))
        self.assertNotIn("Bad Request", json.dumps(self.engine.state))
        record = json.dumps(
            {"type": "response_item", "payload": {"error": GENERIC_BAD_REQUEST}}
        ).encode()
        self.assertIsNone(w.metadata(record))

    def test_mixed_failures_cannot_exceed_global_limit(self):
        self.emit("task_started")
        errors = [
            GENERIC_BAD_REQUEST,
            {"codex_error_info": "server_overloaded"},
            GENERIC_BAD_REQUEST,
            {"codex_error_info": "server_overloaded"},
            {"codex_error_info": "server_overloaded"},
            GENERIC_BAD_REQUEST,
        ]
        for index, error in enumerate(errors):
            turn = TURN if index == 0 else "mixed-" + str(index)
            self.emit("task_complete", turn, error=error)
            if index == 5:
                self.assertNotIn(TID, self.engine.state["jobs"])
                break
            self.engine.dispatch(TID, lambda: None, FakeIPC)
            self.own_start("mixed-" + str(index + 1))
        self.assertEqual(FakeIPC.count, 5)

    def test_historical_bad_requests_are_not_replayed_on_startup(self):
        self.failure()
        fresh = w.Engine(self.engine.cfg, clock=lambda: self.clock[0])
        fresh.scan()
        self.assertFalse(fresh.state["jobs"])

    def test_stopped_goal_or_outage_deadline_prevents_bad_request_retry(self):
        self.emit("thread_goal_updated", goal={"status": "paused"})
        self.failure()
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.emit("thread_goal_updated", goal=None)
        self.emit(
            "task_started", "new", turn_attribution={"turn_trigger": "composer"}
        )
        self.emit("task_complete", "new", error=GENERIC_BAD_REQUEST)
        self.assertIn(TID, self.engine.state["jobs"])
        self.clock[0] += 901
        self.assertEqual(
            self.engine.dispatch(TID, lambda: None, FakeIPC), "outage_timeout"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_lowered_bad_request_limit_applies_to_existing_queue(self):
        self.failure()
        self.engine.state["threads"][TID]["bad_request_attempts"] = 1
        self.engine.cfg["bad_request_max_attempts"] = 1
        self.assertEqual(
            self.engine.dispatch(TID, lambda: None, FakeIPC), "retry_limit"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_native_retry_notice_cannot_reset_attempts_or_outage_budget(self):
        self.failure()
        self.engine.dispatch(TID, lambda: None, FakeIPC)
        self.own_start("automatic")
        outage = self.engine.state["threads"][TID]["outage_at"]
        self.emit(
            "task_complete", "automatic", error=GENERIC_BAD_REQUEST, willRetry=True
        )
        thread = self.engine.state["threads"][TID]
        self.assertEqual(thread["attempts"], 1)
        self.assertEqual(thread["bad_request_attempts"], 1)
        self.assertEqual(thread["outage_at"], outage)
        self.assertEqual(w.latest_lifecycle(self.path)["kind"], "task_started")
        self.emit("task_complete", "automatic", error=GENERIC_BAD_REQUEST)
        self.assertEqual(self.engine.state["jobs"][TID]["due"], self.clock[0] + 5)

    def test_native_retry_after_queued_failure_blocks_dispatch(self):
        self.failure()
        self.emit("task_complete", error=GENERIC_BAD_REQUEST, willRetry=True)
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.assertEqual(w.latest_lifecycle(self.path)["kind"], "task_started")
        self.assertEqual(FakeIPC.count, 0)

    def test_upgrade_deduplication_does_not_reinterpret_consumed_bad_request(self):
        self.failure()
        events, _ = w.read_records(self.path)
        legacy = {
            k: v for k, v in events[-1].items()
            if k not in ("recoverable", "failure_kind")
        }
        legacy_key = hashlib.sha256(
            json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        state = json.loads(json.dumps(self.engine.state))
        state["jobs"].clear()
        state["threads"][TID]["recent_event_keys"] = [legacy_key]
        restored = w.Engine(
            self.engine.cfg, state, clock=lambda: self.clock[0]
        )
        restored.event(TID, self.path, events[-1])
        self.assertNotIn(TID, restored.state["jobs"])


if __name__ == "__main__":
    unittest.main()
