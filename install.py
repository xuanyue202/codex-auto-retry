#!/usr/bin/env python3
"""Install or upgrade a private, per-user Codex Auto Retry LaunchAgent."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import sys
import tempfile
import time
import uuid

import retry_watchdog as watchdog
from codex_retry import __version__, doctor, service
from codex_retry.service import healthy_installation
from codex_retry.storage import (
    DATA,
    RecoveryError,
    atomic_json,
    config,
    load_json,
    mutation_lock,
)

SOURCE = Path(__file__).resolve().parent
RUNTIME_FILES = (
    "retry_watchdog.py",
    "control.py",
    "README.md",
    "LICENSE",
    "LICENSE.upstream",
    "NOTICE",
    "codex_retry/__init__.py",
    "codex_retry/storage.py",
    "codex_retry/lifecycle.py",
    "codex_retry/ipc.py",
    "codex_retry/service.py",
    "codex_retry/doctor.py",
)


def atomic_bytes(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def source_manifest(source=SOURCE):
    files = {
        name: hashlib.sha256((source / name).read_bytes()).hexdigest()
        for name in RUNTIME_FILES
    }
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"version": __version__, "sha256": digest, "files": files}


def stage_release(data, source=SOURCE):
    manifest = source_manifest(source)
    releases = data / "releases"
    releases.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = releases / (__version__ + "-" + manifest["sha256"][:12])
    if target.exists():
        for name, expected in manifest["files"].items():
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != expected:
                raise RecoveryError("installed_release_hash_mismatch")
        return target, manifest
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=releases) as tmp:
        stage = Path(tmp) / "release"
        stage.mkdir(mode=0o700)
        for name in RUNTIME_FILES:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
            os.chmod(destination, 0o600)
        atomic_json(stage / "manifest.json", manifest)
        os.rename(stage, target)
    return target, manifest


def dispatch_idle(state):
    return (
        not state.get("jobs")
        and not state.get("operation")
        and not any(t.get("awaiting_start") for t in state.get("threads", {}).values())
    )


def launch_plist(release):
    return {
        "Label": service.LABEL,
        "ProgramArguments": [
            str(Path(sys.executable).resolve()),
            str(release / "retry_watchdog.py"),
            "run",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "WorkingDirectory": str(release),
        "StandardOutPath": str(DATA / "daemon.stdout.log"),
        "StandardErrorPath": str(DATA / "daemon.stderr.log"),
        "ProcessType": "Background",
    }


def wait_for_unlock(lock, seconds=10):
    deadline = time.monotonic() + seconds
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise RecoveryError("previous_daemon_did_not_exit")
            time.sleep(0.1)


def install(thread=None):
    if sys.platform != "darwin" or sys.version_info < (3, 10):
        raise RecoveryError("requires_macos_and_python_3_10")
    cfg_path, state_path = DATA / "config.json", DATA / "state.json"
    cfg = config(DATA)
    compatibility = doctor.check(cfg, thread)
    state = load_json(state_path, None)
    if state is not None:
        watchdog.Engine(cfg, json.loads(json.dumps(state)))
        if not dispatch_idle(state):
            raise RecoveryError("pending_recovery_reconcile_before_upgrade")
    agent = service.agent_path()
    old_agent = agent.read_bytes() if agent.exists() else None
    if old_agent and not service.owned_agent(plistlib.loads(old_agent)):
        raise RecoveryError("refusing_unrelated_launchagent")
    old_cfg = cfg_path.read_bytes() if cfg_path.exists() else None
    wrapper = Path.home() / ".local/bin/codex-retry"
    old_wrapper = wrapper.read_bytes() if wrapper.exists() else None
    if old_wrapper and b"# codex-auto-retry managed launcher" not in old_wrapper:
        raise RecoveryError("refusing_unrelated_executable")
    was_loaded = service.service_info()["loaded"]
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    with mutation_lock(DATA):
        if (cfg_path.read_bytes() if cfg_path.exists() else None) != old_cfg or (
            agent.read_bytes() if agent.exists() else None
        ) != old_agent:
            raise RecoveryError("installation_inputs_changed_retry_from_current_state")
        release, manifest = stage_release(DATA)
        backup = (
            DATA
            / "backups"
            / ("install-" + time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6])
        )
        backup.mkdir(mode=0o700, parents=True)
        started = time.time()
        try:
            service.stop_service()
            with (DATA / "daemon.lock").open("a") as daemon_lock:
                wait_for_unlock(daemon_lock)
                state = load_json(state_path, {})
                if not dispatch_idle(state):
                    raise RecoveryError("recovery_became_pending_during_upgrade")
                for name in ("config.json", "state.json"):
                    if (DATA / name).exists():
                        shutil.copy2(DATA / name, backup / name)
                        os.chmod(backup / name, 0o600)
                if old_agent:
                    atomic_bytes(backup / "launchagent.plist", old_agent)
                if old_cfg is None:
                    cfg.update(enabled=True, enabled_epoch=str(uuid.uuid4()))
                    atomic_json(cfg_path, cfg)
                for name in ("daemon.stdout.log", "daemon.stderr.log"):
                    path = DATA / name
                    path.touch(mode=0o600, exist_ok=True)
                    os.chmod(path, 0o600)
                atomic_bytes(agent, plistlib.dumps(launch_plist(release)))
                launcher = (
                    "#!/bin/sh\n# codex-auto-retry managed launcher\nexec "
                    + shlex.quote(str(Path(sys.executable).resolve()))
                    + " "
                    + shlex.quote(str(release / "control.py"))
                    + ' "$@"\n'
                )
                atomic_bytes(wrapper, launcher.encode(), 0o700)
            service.start_service()
            for _ in range(30):
                current = load_json(state_path, {})
                health = service.status(cfg, current)
                if (
                    healthy_installation(current, cfg, started, time.time())
                    and health["daemon_alive"]
                    and health["heartbeat_fresh"]
                ):
                    receipt = {
                        "installed": True,
                        "version": __version__,
                        "runtime": str(release),
                        "manifest": manifest,
                        "compatibility": compatibility,
                        "backup": str(backup),
                        "health": health,
                        "upgraded": old_cfg is not None,
                        "config_preserved": old_cfg is None
                        or cfg_path.read_bytes() == old_cfg,
                        "installed_at": time.time(),
                    }
                    atomic_json(DATA / "installation-receipt.json", receipt)
                    return receipt
                time.sleep(0.5)
            raise RecoveryError("new_daemon_heartbeat_missing")
        except Exception:
            # Never rewind live state/seen on rollback: doing so risks duplicate sends.
            service.stop_service()
            if old_agent:
                atomic_bytes(agent, old_agent)
            else:
                agent.unlink(missing_ok=True)
            if old_wrapper:
                atomic_bytes(wrapper, old_wrapper, 0o700)
            else:
                wrapper.unlink(missing_ok=True)
            if old_cfg is None:
                atomic_json(cfg_path, dict(cfg, enabled=False))
            if was_loaded and old_agent:
                service.start_service()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread-id", default=os.environ.get("CODEX_THREAD_ID"))
    args = parser.parse_args()
    print(json.dumps(install(args.thread_id), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RecoveryError, OSError, ValueError) as exc:
        print(f"Installation stopped: {exc}", file=sys.stderr)
        raise SystemExit(1)
