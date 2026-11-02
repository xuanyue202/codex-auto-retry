import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import retry_watchdog as w
from test_retry_watchdog import FakeIPC, TID, TURN


class MissingOwnerIPC(FakeIPC):
    available = False
    before_owner = None

    def owner(self, tid):
        if type(self).before_owner:
            type(self).before_owner()
        if not type(self).available:
            raise w.RecoveryError("ipc_transport_unavailable")
        return super().owner(tid)


class OwnerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / ("rollout-" + TID + ".jsonl")
        self.path.touch()
        self.clock = [1000.0]
        self.cfg = dict(w.DEFAULT, sessions_root=self.tmp.name)
        self.engine = w.Engine(self.cfg, clock=lambda: self.clock[0])
        self.activations, self.saved = [], []
        MissingOwnerIPC.available = False
        MissingOwnerIPC.before_owner = None
        MissingOwnerIPC.count = 0
        MissingOwnerIPC.fail = False
        MissingOwnerIPC.active_before_dispatch = None
        self.emit("task_started")
        self.emit("task_complete", error={"codex_error_info": "server_overloaded"})

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
        self.saved.append(json.loads(json.dumps(self.engine.state)))

    def activate(self, tid):
        claim = self.saved[-1]["threads"][tid]["owner_activation"]
        self.assertEqual(claim["failed_turn_id"], TURN)
        self.assertEqual(claim["result"], "intent_persisted")
        self.assertEqual(self.saved[-1]["operation"]["phase"], "activate_owner")
        self.assertNotIn(tid + ":" + TURN, self.saved[-1]["seen"])
        self.activations.append(tid)

    def dispatch(self, **fields):
        with patch.object(
            w,
            "activate_desktop_thread",
            side_effect=fields.pop("owner_activator", self.activate),
            create=True,
        ):
            return self.engine.dispatch(TID, self.persist, MissingOwnerIPC, **fields)

    def test_missing_owner_is_loaded_once_then_original_failure_dispatches_once(self):
        self.assertEqual(self.dispatch(), "owner_activation_requested")
        self.assertEqual(self.activations, [TID])
        self.assertEqual(MissingOwnerIPC.count, 0)
        self.assertEqual(self.engine.state["threads"][TID].get("attempts", 0), 0)
        outage = self.engine.state["jobs"][TID]["outage_at"]
        MissingOwnerIPC.available = True
        self.clock[0] += 2
        self.assertEqual(self.dispatch(), "acknowledged")
        self.assertEqual(MissingOwnerIPC.count, 1)
        self.assertEqual(self.engine.state["threads"][TID]["outage_at"], outage)
        self.assertEqual(
            self.engine.state["threads"][TID]["owner_activation"]["result"],
            "owner_observed",
        )

    def test_restart_or_continued_owner_failure_does_not_reopen_page(self):
        self.dispatch()
        self.engine = w.Engine(
            self.cfg,
            json.loads(json.dumps(self.engine.state)),
            clock=lambda: self.clock[0],
        )
        self.clock[0] += 35
        self.assertEqual(self.dispatch(), "desktop_route_unavailable")
        self.assertEqual(self.activations, [TID])
        self.assertEqual(MissingOwnerIPC.count, 0)

    def test_crash_after_activation_intent_does_not_repeat_it(self):
        def crash(tid):
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.dispatch(owner_activator=crash)
        self.engine = w.Engine(
            self.cfg,
            json.loads(json.dumps(self.engine.state)),
            clock=lambda: self.clock[0],
        )
        self.assertEqual(self.dispatch(), "desktop_route_unavailable")
        self.assertEqual(self.activations, [])

    def test_owner_that_already_exists_needs_no_activation(self):
        MissingOwnerIPC.available = True
        self.assertEqual(self.dispatch(), "acknowledged")
        self.assertEqual(self.activations, [])

    def test_protocol_error_is_not_treated_as_missing_owner(self):
        with patch.object(
            MissingOwnerIPC,
            "owner",
            side_effect=w.RecoveryError("invalid_ipc_response"),
        ):
            self.assertEqual(self.dispatch(), "desktop_route_unavailable")
        self.assertEqual(self.activations, [])

    def test_new_input_during_discovery_prevents_activation(self):
        MissingOwnerIPC.before_owner = lambda: self.emit("user_message")
        self.assertEqual(self.dispatch(), "superseded_or_active")
        self.assertEqual(self.activations, [])
        self.assertEqual(MissingOwnerIPC.count, 0)

    def test_pause_during_discovery_prevents_activation(self):
        self.assertEqual(
            self.dispatch(may_dispatch=lambda: False), "superseded_or_active"
        )
        self.assertEqual(self.activations, [])

    def test_cancellation_after_load_prevents_turn_send(self):
        self.dispatch()
        self.emit("user_message")
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.assertEqual(MissingOwnerIPC.count, 0)

    def test_explicit_opt_out_does_not_open_page(self):
        self.cfg["recover_missing_owners"] = False
        self.assertEqual(self.dispatch(), "desktop_route_unavailable")
        self.assertEqual(self.activations, [])

    def test_elapsed_budget_prevents_page_activation(self):
        self.clock[0] += self.cfg["max_outage_seconds"]
        self.assertEqual(self.dispatch(), "outage_timeout")
        self.assertEqual(self.activations, [])

    def test_failed_open_is_not_repeated_or_counted_as_provider_attempt(self):
        def failed(tid):
            self.activate(tid)
            raise w.RecoveryError("desktop_activation_failed")

        self.assertEqual(
            self.dispatch(owner_activator=failed), "desktop_route_unavailable"
        )
        self.assertEqual(self.dispatch(), "desktop_route_unavailable")
        self.assertEqual(self.activations, [TID])
        self.assertNotIn(TID + ":" + TURN, self.engine.state["seen"])
        self.assertEqual(MissingOwnerIPC.count, 0)

    def test_ambiguous_turn_delivery_does_not_reactivate_or_resend(self):
        self.dispatch()
        MissingOwnerIPC.available = True
        MissingOwnerIPC.fail = True
        self.assertEqual(self.dispatch(), "dispatch_outcome_unknown")
        self.assertNotIn(TID, self.engine.state["jobs"])
        self.assertEqual(MissingOwnerIPC.count, 1)
        self.assertEqual(self.activations, [TID])


class ActivationCommandTests(unittest.TestCase):
    def test_public_deeplink_has_only_validated_thread_id_and_local_host(self):
        from codex_retry import owner_recovery

        with patch.object(owner_recovery.sys, "platform", "darwin"), patch.object(
            owner_recovery.subprocess, "run"
        ) as run:
            run.return_value.returncode = 0
            owner_recovery.activate_desktop_thread(TID)
            self.assertEqual(
                run.call_args.args[0],
                [
                    "/usr/bin/open",
                    "-g",
                    "-b",
                    "com.openai.codex",
                    "codex://threads/" + TID + "?hostId=local",
                ],
            )
            self.assertFalse(run.call_args.kwargs.get("shell", False))
            with self.assertRaises(w.RecoveryError):
                owner_recovery.activate_desktop_thread(TID + "?prompt=bad")
            self.assertEqual(run.call_count, 1)
