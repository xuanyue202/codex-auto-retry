import json
from pathlib import Path
import plistlib
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install
from codex_retry import doctor, service
from codex_retry.storage import DEFAULT, RecoveryError, atomic_json, load_json


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.data = self.home / "data"
        self.agent = self.home / "agents/retry.plist"
        self.loaded = [False]
        self.starts = []
        patches = [
            patch.object(install, "DATA", self.data),
            patch.object(service, "DATA", self.data),
            patch.object(install.sys, "platform", "darwin"),
            patch.object(install.Path, "home", return_value=self.home),
            patch.object(service, "agent_path", return_value=self.agent),
            patch.object(doctor, "check", return_value={"mutating_requests": 0}),
            patch.object(
                service,
                "service_info",
                side_effect=lambda: {"loaded": self.loaded[0], "pid": 42},
            ),
            patch.object(
                service,
                "stop_service",
                side_effect=lambda: self.loaded.__setitem__(0, False),
            ),
            patch.object(service, "start_service", side_effect=self.start),
            patch.object(service.os, "kill"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def start(self):
        self.starts.append(self.agent.read_bytes())
        self.loaded[0] = True
        cfg = install.config(self.data)
        state = load_json(self.data / "state.json", {})
        state.update(
            enabled_epoch=cfg["enabled_epoch"],
            health={"enabled": cfg["enabled"], "heartbeat": time.time(), "pid": 42},
        )
        atomic_json(self.data / "state.json", state)

    def legacy(self, enabled=True):
        cfg = dict(DEFAULT, enabled=enabled, enabled_epoch="preserve-this")
        atomic_json(self.data / "config.json", cfg)
        state = {
            "version": 1,
            "metadata_version": 2,
            "files": {},
            "threads": {},
            "jobs": {},
            "seen": {"synthetic:failure": time.time()},
            "health": {},
            "enabled_epoch": "preserve-this",
        }
        atomic_json(self.data / "state.json", state)
        self.agent.parent.mkdir(parents=True)
        self.agent.write_bytes(
            plistlib.dumps(
                {
                    "Label": service.LABEL,
                    "ProgramArguments": [
                        "/usr/bin/python3",
                        str(self.data / "runtime/retry_watchdog.py"),
                        "run",
                    ],
                }
            )
        )
        self.loaded[0] = True
        return cfg, state

    def test_clean_install_has_no_private_proof_requirement_and_checks_health(self):
        receipt = install.install()
        self.assertTrue(receipt["installed"])
        self.assertFalse(receipt["upgraded"])
        self.assertTrue(receipt["health"]["enabled"])
        self.assertTrue(receipt["health"]["daemon_alive"])
        runtime = Path(receipt["runtime"])
        self.assertTrue((runtime / "codex_retry/ipc.py").is_file())
        self.assertTrue((self.home / ".local/bin/codex-retry").exists())

    def test_upgrade_preserves_config_bytes_disabled_preference_and_seen(self):
        cfg, old = self.legacy(False)
        before = (self.data / "config.json").read_bytes()
        receipt = install.install()
        self.assertTrue(receipt["upgraded"])
        self.assertTrue(receipt["config_preserved"])
        self.assertFalse(receipt["health"]["enabled"])
        self.assertEqual((self.data / "config.json").read_bytes(), before)
        self.assertEqual(load_json(self.data / "state.json", {})["seen"], old["seen"])

    def test_start_failure_restores_old_agent_without_rewinding_seen(self):
        self.legacy()
        old_agent = self.agent.read_bytes()

        def fail_first_start():
            if not self.starts:
                self.starts.append(b"failed")
                state = load_json(self.data / "state.json", {})
                state["seen"]["new:intent"] = time.time()
                atomic_json(self.data / "state.json", state)
                raise RecoveryError("synthetic_bootstrap_failure")
            self.start()

        with patch.object(service, "start_service", side_effect=fail_first_start):
            with self.assertRaisesRegex(RecoveryError, "synthetic_bootstrap_failure"):
                install.install()
        self.assertEqual(self.agent.read_bytes(), old_agent)
        self.assertTrue(self.loaded[0])
        self.assertIn("new:intent", load_json(self.data / "state.json", {})["seen"])

    def test_pending_recovery_refuses_upgrade_before_service_stop(self):
        self.legacy()
        state = load_json(self.data / "state.json", {})
        state["jobs"]["synthetic"] = {"turn_id": "failure"}
        atomic_json(self.data / "state.json", state)
        with self.assertRaisesRegex(RecoveryError, "pending_recovery"):
            install.install()
        self.assertTrue(self.loaded[0])
        self.assertFalse(self.starts)

    def test_unrelated_label_is_never_overwritten(self):
        self.agent.parent.mkdir(parents=True)
        contents = plistlib.dumps({"Label": "someone.else"})
        self.agent.write_bytes(contents)
        with self.assertRaisesRegex(RecoveryError, "unrelated_launchagent"):
            install.install()
        self.assertEqual(self.agent.read_bytes(), contents)

    def test_unrelated_launcher_is_never_overwritten(self):
        wrapper = self.home / ".local/bin/codex-retry"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text("unrelated executable")
        with self.assertRaisesRegex(RecoveryError, "unrelated_executable"):
            install.install()
        self.assertEqual(wrapper.read_text(), "unrelated executable")

    def test_staging_is_idempotent_and_detects_modified_runtime(self):
        release, manifest = install.stage_release(self.data)
        self.assertEqual(install.stage_release(self.data), (release, manifest))
        (release / "retry_watchdog.py").write_text("tampered")
        with self.assertRaisesRegex(RecoveryError, "hash_mismatch"):
            install.stage_release(self.data)

    def test_read_only_compatibility_failure_leaves_installation_untouched(self):
        with patch.object(
            doctor, "check", side_effect=RecoveryError("desktop_owner_unavailable")
        ):
            with self.assertRaisesRegex(RecoveryError, "owner_unavailable"):
                install.install("11111111-2222-3333-4444-555555555555")
        self.assertFalse(self.data.exists())
        self.assertFalse(self.agent.exists())


class ServiceTests(unittest.TestCase):
    def test_old_heartbeat_does_not_mean_dead_process(self):
        state = {"health": {"pid": 42, "heartbeat": 80}, "threads": {}, "jobs": {}}
        with patch.object(
            service, "service_info", return_value={"loaded": True, "pid": 42}
        ), patch.object(service.os, "kill"):
            result = service.status(DEFAULT, state, now=100)
        self.assertTrue(result["daemon_alive"])
        self.assertFalse(result["heartbeat_fresh"])

    def test_legacy_and_versioned_agent_ownership_are_precise(self):
        data = Path("/synthetic/private/data")
        for script, expected in [
            (data / "runtime/retry_watchdog.py", True),
            (data / "releases/version/retry_watchdog.py", True),
            (Path("/other/retry_watchdog.py"), False),
        ]:
            value = {
                "Label": service.LABEL,
                "ProgramArguments": ["/python", str(script), "run"],
            }
            self.assertEqual(service.owned_agent(value, data), expected)


class DoctorTests(unittest.TestCase):
    def test_checks_owner_and_settings_without_any_mutating_method(self):
        fake = SimpleNamespace(
            owner=lambda tid: "owner",
            settings=lambda *a: {"model": "test"},
            close=lambda: None,
        )
        with patch.object(doctor.sys, "platform", "darwin"), patch.object(
            doctor, "DesktopIPC", return_value=fake
        ):
            result = doctor.check(DEFAULT, "11111111-2222-3333-4444-555555555555")
        self.assertTrue(result["owner_available"])
        self.assertEqual(result["mutating_requests"], 0)
