import json
from pathlib import Path
import tempfile
import unittest

import retry_watchdog as w
from test_retry_watchdog import FakeIPC, TID, TURN


class RecoveryInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ("rollout-" + TID + ".jsonl")
        self.path.touch()
        self.clock = [1000.0]
        self.engine = w.Engine(
            dict(w.DEFAULT, sessions_root=self.tmp.name), clock=lambda: self.clock[0]
        )
        FakeIPC.count, FakeIPC.fail, FakeIPC.active_before_dispatch = 0, False, None

    def emit(self, kind, turn=TURN, **extra):
        self.clock[0] += 1
        stamp = w.dt.datetime.fromtimestamp(
            self.clock[0], w.dt.timezone.utc
        ).isoformat()
        raw = (
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": stamp,
                    "payload": {"type": kind, "turn_id": turn, **extra},
                }
            )
            + "\n"
        ).encode()
        with self.path.open("ab") as stream:
            stream.write(raw)
        self.engine.event(TID, self.path, w.metadata(raw))

    def dispatch_failure(self):
        self.emit("task_started")
        self.emit("task_complete", error={"codex_error_info": "server_overloaded"})
        states = []
        self.engine.dispatch(
            TID,
            lambda: states.append(json.loads(json.dumps(self.engine.state))),
            FakeIPC,
        )
        return states

    def own_start(self, turn="automatic"):
        identifier = self.engine.state["threads"][TID]["last_dispatch"]["recovery_id"]
        self.emit(
            "task_started",
            turn,
            turn_attribution={"turn_trigger": "codex_auto_retry_" + identifier},
        )
        return identifier

    def test_identity_is_persisted_before_send_and_matches_observed_start(self):
        states = self.dispatch_failure()
        intent = next(
            s["threads"][TID]["last_dispatch"]
            for s in states
            if s["threads"][TID].get("last_dispatch", {}).get("result")
            == "intent_persisted"
        )
        self.assertTrue(intent["recovery_id"])
        self.assertEqual(intent["recovery_id"], FakeIPC.last_recovery_id)
        self.own_start()
        self.assertEqual(
            self.engine.state["threads"][TID]["last_dispatch"]["result"],
            "new_turn_observed",
        )

    def test_automatic_message_keeps_budget_and_remains_retryable(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.own_start()
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.assertEqual(self.engine.state["threads"][TID]["attempts"], 1)
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertIn(TID, self.engine.state["jobs"])
        self.assertEqual(self.engine.state["jobs"][TID]["due"], self.clock[0] + 2)
        self.assertEqual(w.latest_lifecycle(self.path)["kind"], "task_complete")

    def test_human_message_even_inside_automatic_turn_still_cancels(self):
        self.dispatch_failure()
        self.own_start()
        self.emit("user_message", "automatic", message="synthetic human interruption")
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_copied_instruction_without_matching_origin_is_not_ignored(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.emit(
            "task_started", "manual", turn_attribution={"turn_trigger": "composer"}
        )
        self.emit("user_message", "manual", message=CONTINUATION_TEXT)
        self.emit(
            "task_complete", "manual", error={"codex_error_info": "server_overloaded"}
        )
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_full_automatic_chain_stops_at_five_despite_user_message_events(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.emit("task_started")
        for index in range(6):
            turn = TURN if index == 0 else "automatic-" + str(index)
            self.emit(
                "task_complete", turn, error={"codex_error_info": "server_overloaded"}
            )
            if index == 5:
                self.assertNotIn(TID, self.engine.state["jobs"])
                break
            self.engine.dispatch(TID, lambda: None, FakeIPC)
            self.own_start("automatic-" + str(index + 1))
            self.emit(
                "user_message", "automatic-" + str(index + 1), message=CONTINUATION_TEXT
            )
        self.assertEqual(FakeIPC.count, 5)

    def test_automatic_origin_survives_restart_without_replaying_send(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.engine = w.Engine(
            self.engine.cfg,
            json.loads(json.dumps(self.engine.state)),
            clock=lambda: self.clock[0],
        )
        self.own_start()
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertEqual(self.engine.state["threads"][TID]["attempts"], 1)
        self.assertEqual(FakeIPC.count, 1)

    def test_unmatched_start_does_not_count_as_our_delivery(self):
        self.dispatch_failure()
        self.emit("task_started", "other", turn_attribution={"turn_trigger": None})
        self.assertNotEqual(
            self.engine.state["threads"][TID]["last_dispatch"]["result"],
            "new_turn_observed",
        )

    def test_automatic_input_does_not_reset_outage_deadline(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.own_start()
        original_outage = self.engine.state["threads"][TID]["outage_at"]
        self.clock[0] += 901
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.assertEqual(
            self.engine.state["threads"][TID]["outage_at"], original_outage
        )
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_static_message_with_foreign_origin_cannot_preserve_our_retry(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.emit(
            "task_started",
            "foreign",
            turn_attribution={
                "turn_trigger": "codex_auto_retry_99999999-2222-3333-4444-555555555555"
            },
        )
        self.emit("user_message", "foreign", message=CONTINUATION_TEXT)
        self.emit(
            "task_complete", "foreign", error={"codex_error_info": "server_overloaded"}
        )
        self.assertNotIn(TID, self.engine.state["jobs"])

    def test_metadata_retains_only_static_input_match_not_message_body(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.emit("user_message", message=CONTINUATION_TEXT)
        events, _ = w.read_records(self.path)
        self.assertTrue(events[-1]["recovery_input"])
        self.assertNotIn(CONTINUATION_TEXT, json.dumps(events))

    def test_human_copy_of_instruction_after_failure_still_cancels_pending_retry(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.own_start()
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertIn(TID, self.engine.state["jobs"])
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.assertEqual(w.latest_lifecycle(self.path)["kind"], "user_message")

    def test_second_identical_input_cancels_even_after_restart(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.own_start()
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.engine = w.Engine(
            self.engine.cfg,
            json.loads(json.dumps(self.engine.state)),
            clock=lambda: self.clock[0],
        )
        self.emit("user_message", None, message=CONTINUATION_TEXT)
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.assertNotEqual(w.latest_lifecycle(self.path)["kind"], "task_complete")

    def test_duplicate_start_preserves_budget_before_initial_input(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.own_start()
        self.own_start()
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.assertEqual(self.engine.state["threads"][TID]["attempts"], 1)
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertIn(TID, self.engine.state["jobs"])

    def test_duplicate_start_does_not_renew_input_exemption(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.own_start()
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.own_start()
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.assertNotEqual(w.latest_lifecycle(self.path)["kind"], "task_complete")

    def test_duplicate_copy_of_same_log_record_is_not_new_human_input(self):
        from codex_retry.recovery_input import CONTINUATION_TEXT

        self.dispatch_failure()
        self.own_start()
        self.emit("user_message", "automatic", message=CONTINUATION_TEXT)
        duplicate = self.path.read_bytes().splitlines(keepends=True)[-1]
        with self.path.open("ab") as stream:
            stream.write(duplicate)
        self.emit(
            "task_complete",
            "automatic",
            error={"codex_error_info": "server_overloaded"},
        )
        self.assertEqual(w.latest_lifecycle(self.path)["kind"], "task_complete")
        self.assertIn(TID, self.engine.state["jobs"])
