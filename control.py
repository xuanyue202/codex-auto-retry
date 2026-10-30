#!/usr/bin/env python3
"""Manage Codex Auto Retry for this macOS user."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import sys
import uuid

from codex_retry import __version__
from codex_retry import doctor, service
from codex_retry.service import status
from codex_retry.storage import (
    DATA,
    RecoveryError,
    atomic_json,
    config,
    load_json,
    mutation_lock,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "command",
        choices=["status", "doctor", "enable", "disable", "stop", "start", "uninstall"],
    )
    parser.add_argument("--thread-id", default=os.environ.get("CODEX_THREAD_ID"))
    args = parser.parse_args()
    cfg = config(DATA)
    if args.command == "doctor":
        print(json.dumps(doctor.check(cfg, args.thread_id), indent=2))
        return
    if args.command == "status":
        print(
            json.dumps(
                dict(
                    status(cfg, load_json(DATA / "state.json", {})), version=__version__
                ),
                indent=2,
            )
        )
        return
    with mutation_lock(DATA):
        apply_command(args.command)


def apply_command(command):
    cfg = config(DATA)
    if command in ("stop", "uninstall", "start"):
        path = service.agent_path()
        if path.exists() and not service.owned_agent(plistlib.loads(path.read_bytes())):
            raise RecoveryError("refusing_unrelated_launchagent")
    if command in ("enable", "disable", "stop", "uninstall"):
        cfg.update(enabled=command == "enable", enabled_epoch=str(uuid.uuid4()))
        atomic_json(DATA / "config.json", cfg)
    if command in ("stop", "uninstall"):
        path = service.agent_path()
        if path.exists() and not service.owned_agent(plistlib.loads(path.read_bytes())):
            raise RecoveryError("refusing_unrelated_launchagent")
        service.stop_service()
        if command == "uninstall":
            path.unlink(missing_ok=True)
            wrapper = Path.home() / ".local/bin/codex-retry"
            if (
                wrapper.exists()
                and "# codex-auto-retry managed launcher" in wrapper.read_text()
            ):
                wrapper.unlink()
            print(json.dumps({"uninstalled": True, "private_data_retained": str(DATA)}))
            return
    elif command == "start":
        if not service.owned_agent(plistlib.loads(service.agent_path().read_bytes())):
            raise RecoveryError("refusing_unrelated_launchagent")
        if not service.service_info()["loaded"]:
            service.start_service()
    print(
        json.dumps(
            dict(status(cfg, load_json(DATA / "state.json", {})), version=__version__),
            indent=2,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (RecoveryError, OSError, ValueError) as exc:
        print(f"codex-retry: {exc}", file=sys.stderr)
        raise SystemExit(1)
