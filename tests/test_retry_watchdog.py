import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest

import retry_watchdog as w
from install import healthy_installation

TID = "11111111-2222-3333-4444-555555555555"
TURN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def raw(kind, turn=TURN, **kwargs):
    return (
        json.dumps(
            {
                "timestamp": "2026-10-29T07:00:00Z",
                "type": "event_msg",
                "payload": {"type": kind, "turn_id": turn, **kwargs},
            }
        )
        + "\n"
    ).encode()


class FakeIPC:
    count = 0
    fail = False
    active_before_dispatch = None

    def __init__(self, path):
        pass

    def owner(self, tid):
        return "desktop-owner"

    def settings(self, tid, owner):
        if self.active_before_dispatch:
            self.active_before_dispatch()
        return {"model": "original-model", "reasoningEffort": "high", "mode": "durable"}

    def continue_thread(self, tid, owner, model, effort):
        type(self).count += 1
        if self.fail:
            raise w.AmbiguousDispatch()
        return {"resultType": "success"}

    def close(self):
        pass


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ("rollout-" + TID + ".jsonl")
        self.path.write_bytes(b"")
        self.cfg = dict(w.DEFAULT)
        self.clock = [1000.0]
        self.e = w.Engine(self.cfg, clock=lambda: self.clock[0])
        FakeIPC.count = 0
        FakeIPC.fail = False
        FakeIPC.active_before_dispatch = None

    def event(self, kind, **kwargs):
        b = raw(kind, **kwargs)
        with self.path.open("ab") as f:
            f.write(b)
        self.e.event(TID, self.path, w.metadata(b))

    def failure(self):
        self.event("task_started")
        self.event(
            "task_complete",
            error={
                "message": "Selected model is at capacity. Please try a different model.",
                "codex_error_info": "server_overloaded",
            },
        )

    def test_capacity_requires_structured_event_and_exact_error(self):
        self.assertTrue(w.classify_capacity({"codex_error_info": "server_overloaded"}))
        for code in [
            "policyCapacityUnavailable",
            "usageLimitExceeded",
            "unauthorized",
            "cyberPolicy",
            "badRequest",
        ]:
            self.assertFalse(
                w.classify_capacity(
                    {
                        "codex_error_info": code,
                        "message": "Selected model is at capacity. Please try a different model.",
                    }
                )
            )
        self.assertIsNone(
            w.metadata(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "message", "text": "server_overloaded"},
                    }
                ).encode()
            )
        )
        self.assertFalse(
            w.classify_capacity(
                "quoted: Selected model is at capacity. Please try a different model."
            )
        )

    def test_backoff_and_same_failure_deduplicated_after_restart(self):
        self.failure()
        self.assertEqual(self.e.state["jobs"][TID]["due"], 1001)
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "acknowledged")
        restored = w.Engine(self.cfg, json.loads(json.dumps(self.e.state)))
        restored.event(
            TID,
            self.path,
            w.metadata(
                raw("task_complete", error={"codex_error_info": "server_overloaded"})
            ),
        )
        self.assertNotIn(TID, restored.state["jobs"])
        self.assertEqual(FakeIPC.count, 1)

    def test_user_cancel_prevents_dispatch(self):
        self.failure()
        self.event("turn_aborted")
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_new_user_input_cancels_countdown(self):
        self.failure()
        self.event("user_message", message="private text not retained")
        self.assertNotIn(TID, self.e.state["jobs"])
        self.assertNotIn("private text", json.dumps(self.e.state))

    def test_active_turn_race_during_read_prevents_start(self):
        self.failure()

        def started():
            with self.path.open("ab") as f:
                f.write(raw("task_started", turn="new-turn"))

        FakeIPC.active_before_dispatch = staticmethod(started)
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC), "superseded_or_active"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_pause_during_read_prevents_start(self):
        self.failure()
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC, may_dispatch=lambda: False),
            "superseded_or_active",
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_ambiguous_dispatch_never_replayed(self):
        self.failure()
        FakeIPC.fail = True
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC), "dispatch_outcome_unknown"
        )
        self.assertNotIn(TID, self.e.state["jobs"])
        self.assertIn(TID + ":" + TURN, self.e.state["seen"])
        self.assertEqual(FakeIPC.count, 1)

    def test_retry_limit(self):
        self.failure()
        self.e.state["threads"][TID]["attempts"] = self.cfg["max_attempts"]
        self.e.state["jobs"].clear()
        self.e.event(
            TID,
            self.path,
            w.metadata(
                raw("task_complete", error={"codex_error_info": "server_overloaded"})
            ),
        )
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_native_goal_not_restarted(self):
        self.event("thread_goal_updated", status="paused")
        self.failure()
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_active_goal_capacity_failure_is_recovered(self):
        self.event("thread_goal_updated", status="active")
        self.failure()
        self.assertIn(TID, self.e.state["jobs"])
        self.assertEqual(self.e.dispatch(TID, lambda: None, FakeIPC), "acknowledged")
        self.assertEqual(FakeIPC.count, 1)

    def test_active_goal_native_next_turn_prevents_duplicate(self):
        self.event("thread_goal_updated", status="active")
        self.failure()
        self.assertIn(TID, self.e.state["jobs"])

        def started():
            with self.path.open("ab") as f:
                f.write(raw("task_started", turn="native-next"))

        FakeIPC.active_before_dispatch = staticmethod(started)
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC), "superseded_or_active"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_nonactive_goal_states_are_never_restarted(self):
        for status in [
            "paused",
            "complete",
            "completed",
            "budget_limited",
            "blocked",
            "unknown",
        ]:
            with self.subTest(status=status):
                self.e = w.Engine(self.cfg, clock=lambda: self.clock[0])
                self.event("thread_goal_updated", status=status)
                self.failure()
                self.assertNotIn(TID, self.e.state["jobs"])

    def test_active_goal_recovery_opt_out_is_respected(self):
        self.cfg["recover_active_goals"] = False
        self.event("thread_goal_updated", status="active")
        self.failure()
        self.assertNotIn(TID, self.e.state["jobs"])
        self.assertEqual(
            self.e.state["threads"][TID]["last_recovery_decision"]["reason"],
            "goal_not_retryable",
        )

    def test_active_goal_still_obeys_attempt_limit(self):
        self.event("thread_goal_updated", status="active")
        self.failure()
        self.e.state["threads"][TID]["attempts"] = self.cfg["max_attempts"]
        self.e.state["jobs"].clear()
        self.e.event(
            TID,
            self.path,
            w.metadata(
                raw("task_complete", error={"codex_error_info": "server_overloaded"})
            ),
        )
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_goal_pause_in_new_shard_prevents_dispatch(self):
        self.failure()
        folder = Path(self.tmp.name) / "2026/10/30"
        folder.mkdir(parents=True)
        newer = folder / ("rollout-" + TID + "_compacted.jsonl")
        newer.write_bytes(
            raw("thread_goal_updated", status="paused").replace(
                b"07:00:00Z", b"07:00:01Z"
            )
        )
        self.e.cfg["sessions_root"] = self.tmp.name
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC), "superseded_or_active"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_new_active_turn_in_new_shard_prevents_dispatch(self):
        self.failure()
        folder = Path(self.tmp.name) / "2026/10/30"
        folder.mkdir(parents=True)
        newer = folder / ("rollout-" + TID + "_compacted.jsonl")
        newer.write_bytes(
            raw("task_started", turn="new-shard-turn").replace(
                b"07:00:00Z", b"07:00:01Z"
            )
        )
        self.e.cfg["sessions_root"] = self.tmp.name
        self.assertEqual(
            self.e.dispatch(TID, lambda: None, FakeIPC), "superseded_or_active"
        )
        self.assertEqual(FakeIPC.count, 0)

    def test_historical_errors_are_baselined_not_recovered(self):
        now = w.dt.datetime.now(w.dt.timezone.utc)
        folder = Path(self.tmp.name) / now.strftime("%Y/%m/%d")
        folder.mkdir(parents=True)
        p = folder / self.path.name
        p.write_bytes(
            raw("task_complete", error={"codex_error_info": "server_overloaded"})
        )
        self.e.cfg["sessions_root"] = self.tmp.name
        self.e.scan()
        self.assertFalse(self.e.state["jobs"])
        with p.open("ab") as f:
            f.write(raw("task_started", turn="next"))
            f.write(
                raw(
                    "task_complete",
                    turn="next",
                    error={"codex_error_info": "server_overloaded"},
                )
            )
        self.e.scan()
        self.assertEqual(self.e.state["jobs"][TID]["turn_id"], "next")

    def test_partial_record_waits_for_newline(self):
        b = raw("task_started")
        self.path.write_bytes(b[:-1])
        records, offset = w.read_records(self.path)
        self.assertEqual((records, offset), ([], 0))
        with self.path.open("ab") as f:
            f.write(b"\n")
        records, offset = w.read_records(self.path)
        self.assertEqual(records[0]["turn_id"], TURN)

    def test_new_task_failing_before_next_poll_is_not_missed(self):
        now = w.dt.datetime.now(w.dt.timezone.utc)
        folder = Path(self.tmp.name) / now.strftime("%Y/%m/%d")
        folder.mkdir(parents=True)
        self.e.cfg["sessions_root"] = self.tmp.name
        self.e.scan()
        p = folder / self.path.name
        p.write_bytes(
            raw("task_started")
            + raw("task_complete", error={"codex_error_info": "server_overloaded"})
        )
        self.e.scan()
        self.assertIn(TID, self.e.state["jobs"])

    def test_old_partition_keeps_watching_new_turns(self):
        day = w.dt.datetime.now(w.dt.timezone.utc) - w.dt.timedelta(days=30)
        folder = Path(self.tmp.name) / day.strftime("%Y/%m/%d")
        folder.mkdir(parents=True)
        p = folder / self.path.name
        p.write_bytes(
            raw("task_complete", error={"codex_error_info": "server_overloaded"})
        )
        self.e.cfg["sessions_root"] = self.tmp.name
        self.e.scan()
        self.assertFalse(self.e.state["jobs"])
        with p.open("ab") as f:
            f.write(raw("task_started", turn="fresh"))
            f.write(
                raw(
                    "task_complete",
                    turn="fresh",
                    error={"codex_error_info": "server_overloaded"},
                )
            )
        self.e.scan()
        self.assertEqual(self.e.state["jobs"][TID]["turn_id"], "fresh")
        self.e.scan()
        self.assertIn(str(p), self.e.state["files"])

    def test_discovery_upgrade_baselines_previously_unwatched_files(self):
        self.e.state["monitor_started_at"] = self.clock[0]
        day = w.dt.datetime.now(w.dt.timezone.utc) - w.dt.timedelta(days=2)
        folder = Path(self.tmp.name) / day.strftime("%Y/%m/%d")
        folder.mkdir(parents=True)
        p = folder / self.path.name
        p.write_bytes(
            raw("task_complete", error={"codex_error_info": "server_overloaded"})
        )
        self.e.cfg["sessions_root"] = self.tmp.name
        self.e.scan()
        self.assertFalse(self.e.state["jobs"])
        self.assertEqual(self.e.state["files"][str(p)]["offset"], p.stat().st_size)
        with p.open("ab") as f:
            f.write(raw("task_started", turn="after-upgrade"))
            f.write(
                raw(
                    "task_complete",
                    turn="after-upgrade",
                    error={"codex_error_info": "server_overloaded"},
                )
            )
        self.e.scan()
        self.assertEqual(self.e.state["jobs"][TID]["turn_id"], "after-upgrade")

    def test_new_file_in_old_partition_is_discovered(self):
        self.e.cfg["sessions_root"] = self.tmp.name
        self.e.scan()
        day = w.dt.datetime.now(w.dt.timezone.utc) - w.dt.timedelta(days=100)
        folder = Path(self.tmp.name) / day.strftime("%Y/%m/%d")
        folder.mkdir(parents=True)
        p = folder / self.path.name
        p.write_bytes(
            raw("task_started")
            + raw("task_complete", error={"codex_error_info": "server_overloaded"})
        )
        self.e.scan()
        self.assertIn(TID, self.e.state["jobs"])

    def test_recently_copied_historical_error_is_not_recovered(self):
        self.e.state["monitor_started_at"] = 9999999999
        self.failure()
        self.assertNotIn(TID, self.e.state["jobs"])

    def test_late_old_completion_does_not_replace_new_active_turn(self):
        self.event("task_started", turn="new")
        self.e.event(
            TID,
            self.path,
            w.metadata(
                raw("task_complete", error={"codex_error_info": "server_overloaded"})
            ),
        )
        self.assertEqual(self.e.state["threads"][TID]["turn_id"], "new")
        self.assertFalse(self.e.state["jobs"])

    def test_continuation_wire_inherits_settings_and_contains_no_prompt(self):
        client = object.__new__(w.DesktopIPC)
        captured = []
        client.request = lambda *args, **kwargs: captured.append((args, kwargs)) or {
            "resultType": "success"
        }
        client.continue_thread(TID, "owner", "original-model", "high")
        args, kwargs = captured[0]
        self.assertEqual(args[:2], ("thread-follower-start-turn", 2))
        req = args[2]["turnStart"]["request"]
        ctx = args[2]["turnStart"]["context"]
        self.assertEqual(req["threadId"], TID)
        self.assertEqual(req["input"], [])
        self.assertEqual(ctx, {"inheritThreadSettings": True, "responseItems": []})
        for forbidden in [
            "approvalPolicy",
            "permissions",
            "sandboxPolicy",
            "cwd",
            "model",
            "collaborationMode",
            "goal",
            "objective",
            "tokenBudget",
            "tokensUsed",
            "timeUsedSeconds",
        ]:
            self.assertNotIn(forbidden, req)
        self.assertTrue(kwargs["mutating"])

    def test_socket_not_world_writable_directory(self):
        # A regular file is never accepted as the Desktop socket.
        with self.assertRaises(w.RecoveryError):
            w.safe_socket(self.path)

    def test_installer_rejects_previous_process_heartbeat(self):
        cfg = {"enabled_epoch": "new", "enabled": True}
        self.assertFalse(
            healthy_installation(
                {"enabled_epoch": "old", "health": {"enabled": True, "heartbeat": 100}},
                cfg,
                99,
                101,
            )
        )
        self.assertFalse(
            healthy_installation(
                {"enabled_epoch": "new", "health": {"enabled": True, "heartbeat": 98}},
                cfg,
                99,
                101,
            )
        )
        self.assertTrue(
            healthy_installation(
                {"enabled_epoch": "new", "health": {"enabled": True, "heartbeat": 100}},
                cfg,
                99,
                101,
            )
        )


if __name__ == "__main__":
    unittest.main()
