"""Bounded framed IPC to the existing Codex Desktop thread owner."""

import json
import os
from pathlib import Path
import socket
import stat
import struct
import time
import uuid
from .storage import RecoveryError, AmbiguousDispatch


def safe_socket(path):
    path = Path(path)
    s, parent = path.lstat(), path.parent.lstat()
    if not stat.S_ISSOCK(s.st_mode) or s.st_uid != os.getuid():
        raise RecoveryError("socket_not_owned_by_current_user")
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o022
    ):
        raise RecoveryError("unsafe_socket_directory")


class DesktopIPC:
    def __init__(self, path, timeout=15):
        safe_socket(path)
        self.sock = socket.socket(socket.AF_UNIX)
        self.timeout = timeout
        self.sock.settimeout(timeout)
        self.sock.connect(str(path))
        self.client = "initializing-client"
        try:
            r = self.request("initialize", 0, {"clientType": "codex-auto-retry-macos"})
            result = r.get("result")
            self.client = result.get("clientId") if isinstance(result, dict) else None
            if not isinstance(self.client, str) or not self.client:
                raise RecoveryError("ipc_initialize_failed")
        except Exception:
            self.close()
            raise

    def close(self):
        self.sock.close()

    def send(self, data):
        data = json.dumps(data, separators=(",", ":")).encode()
        self.sock.sendall(struct.pack("<I", len(data)) + data)

    def receive(self, deadline=None):
        def exact(n):
            data = bytearray()
            while len(data) < n:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("ipc_deadline")
                    self.sock.settimeout(remaining)
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    raise RecoveryError("ipc_disconnected")
                data.extend(chunk)
            return bytes(data)

        size = struct.unpack("<I", exact(4))[0]
        if not 0 < size <= 16 * 1024 * 1024:
            raise RecoveryError("invalid_ipc_frame")
        return json.loads(exact(size))

    def request(self, method, version, params, target=None, mutating=False):
        rid = str(uuid.uuid4())
        data = {
            "type": "request",
            "requestId": rid,
            "sourceClientId": self.client,
            "version": version,
            "method": method,
            "params": params,
        }
        if target:
            data["targetClientId"] = target
        try:
            deadline = time.monotonic() + self.timeout
            self.sock.settimeout(self.timeout)
            self.send(data)
            while time.monotonic() < deadline:
                r = self.receive(deadline)
                if not isinstance(r, dict):
                    raise RecoveryError("invalid_ipc_response")
                if r.get("type") == "client-discovery-request":
                    self.send(
                        {
                            "type": "client-discovery-response",
                            "requestId": r["requestId"],
                            "response": {"canHandle": False},
                        }
                    )
                    continue
                if r.get("requestId") != rid:
                    continue
                if r.get("resultType") != "success":
                    raise RecoveryError(
                        "ipc_" + str(r.get("error", "request_failed"))[:120]
                    )
                return r
            raise TimeoutError("ipc_deadline")
        except (OSError, ValueError, TimeoutError, RecoveryError) as exc:
            if mutating:
                raise AmbiguousDispatch("dispatch_outcome_unknown") from exc
            if isinstance(exc, RecoveryError):
                raise
            raise RecoveryError("ipc_transport_unavailable") from exc

    def owner(self, tid):
        r = self.request(
            "thread-owner-discovery", 1, {"hostId": "local", "conversationId": tid}
        )
        owner = r.get("handledByClientId")
        if not owner:
            raise RecoveryError("desktop_owner_unavailable")
        return owner

    def settings(self, tid, owner):
        r = self.request(
            "thread-follower-read-model-settings", 1, {"conversationId": tid}, owner
        )
        result = r.get("result")
        settings = result.get("settings") if isinstance(result, dict) else None
        if (
            not isinstance(settings, dict)
            or not isinstance(settings.get("model"), str)
            or not settings["model"]
        ):
            raise RecoveryError("thread_settings_unavailable")
        return settings

    def continue_thread(self, tid, owner, model, effort):
        # Inherit CWD, permissions, environment, provider and other existing settings
        # in the Desktop owner. Never map an unknown profile to full access.
        request = {
            "threadId": tid,
            "input": [],
            "clientUserMessageId": str(uuid.uuid4()),
        }
        return self.request(
            "thread-follower-start-turn",
            2,
            {
                "conversationId": tid,
                "turnStart": {
                    "request": request,
                    "context": {"inheritThreadSettings": True, "responseItems": []},
                },
            },
            owner,
            mutating=True,
        )
