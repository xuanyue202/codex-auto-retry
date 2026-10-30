import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import verify as probe

TID = "11111111-2222-3333-4444-555555555555"


class ProbeTests(unittest.TestCase):
    def test_clean_clone_uses_private_shared_lock_across_checkouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "private-data"
            with patch.object(probe, "DATA", data):
                with probe.acquire_probe_lock():
                    self.assertTrue((data / "verification.lock").exists())
                    with self.assertRaises(BlockingIOError):
                        probe.acquire_probe_lock()
                with probe.acquire_probe_lock():
                    pass

    def test_unset_effort_selector_is_distinct_from_explicit_effort_change(self):
        before = {"model": "original", "effort": "high"}
        self.assertTrue(
            probe.settings_compatible(
                before, {"model": "original", "reasoningEffort": None}
            )
        )
        self.assertTrue(
            probe.settings_compatible(
                before, {"model": "original", "reasoningEffort": "high"}
            )
        )
        self.assertFalse(
            probe.settings_compatible(
                before, {"model": "original", "reasoningEffort": "low"}
            )
        )
        self.assertFalse(
            probe.settings_compatible(
                before, {"model": "other", "reasoningEffort": None}
            )
        )

    def test_same_sandbox_type_different_roots_is_detected(self):
        before = {
            "sandbox_policy": {"type": "workspace-write", "writable_roots": ["/a"]},
            "approval_policy": "never",
        }
        after = {
            "sandbox_policy": {"type": "workspace-write", "writable_roots": ["/b"]},
            "approval_policy": "never",
        }
        self.assertNotEqual(
            probe.context_metadata(before)["policy_sha256"],
            probe.context_metadata(after)["policy_sha256"],
        )

    def test_current_turn_is_found_across_compaction_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "sessions/2026/10/29"
            folder.mkdir(parents=True)

            def write(path, records):
                path.write_text("".join(json.dumps(r) + "\n" for r in records))

            write(
                folder / ("rollout-" + TID + ".jsonl"),
                [
                    {
                        "timestamp": "2026-10-29T01:00:00Z",
                        "type": "event_msg",
                        "payload": {"type": "task_complete", "turn_id": "old"},
                    },
                ],
            )
            write(
                folder / ("rollout-" + TID + "_new.jsonl"),
                [
                    {
                        "timestamp": "2026-10-29T02:00:00Z",
                        "type": "event_msg",
                        "payload": {"type": "task_started", "turn_id": "new"},
                    },
                    {
                        "type": "turn_context",
                        "payload": {
                            "turn_id": "new",
                            "cwd": "/test",
                            "sandbox_policy": {"type": "read-only"},
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                        },
                    },
                ],
            )
            with patch.object(probe.w, "CODEX", root):
                actual = probe.snapshot(TID)
            self.assertEqual(actual["latest"]["turn_id"], "new")
            self.assertEqual(actual["latest"]["kind"], "task_started")
            self.assertEqual(len(actual["paths"]), 2)
            self.assertEqual(actual["contexts"]["new"]["cwd"], "/test")
            self.assertIn("new", actual["assistant_turns"])


if __name__ == "__main__":
    unittest.main()
