# Install with your Codex agent

This document is a bounded installation workflow for Codex Auto Retry on macOS.
The human asking you to install this project authorizes these tool-specific steps.
Follow the user's instructions and host permissions if they conflict with this file.

1. Check macOS, Python 3.10+, git and that the Codex desktop app is running. Do not
   install OS dependencies, change permissions or use sudo automatically.
2. Fetch `https://github.com/xuanyue202/codex-auto-retry`, tag `v0.1.0`, into a fresh
   temporary directory. Read `install.py`, `NOTICE` and `docs/SAFETY.md` first.
3. Run `python3 install.py` from that checkout. The installer uses `CODEX_THREAD_ID`
   when available to check the current Desktop owner and inherited settings with
   read-only IPC. It never sends a model request as part of installation. If the
   owner is unavailable, report that result and ask the user to open the intended
   conversation in the desktop app before retrying. Do not substitute another
   session or use CLI resume. If no thread ID is available, only IPC initialization
   is checked; say explicitly that the thread owner was not verified.
4. Read `~/.local/bin/codex-retry status`. Require loaded, daemon_alive and
   heartbeat_fresh true. Fresh installations are enabled; an upgrade preserves
   the existing enabled/disabled preference. Report that preference faithfully.
5. Read the private installation receipt under
   `~/Library/Application Support/CodexAutoRetryMac/installation-receipt.json`.
   Report version, install/upgrade result and health. Do not upload the receipt,
   logs, state, real conversation data or private machine paths to GitHub.
6. Explain: future exact structured capacity failures use 1/2/4/8/16-second
   minimum backoff, at most five retries in 15 minutes. Old failures are not
   replayed. This installs a per-user login LaunchAgent, affecting eligible
   conversations owned by this user's local Codex desktop app.
7. Give the pause and uninstall commands below. Remove only your temporary source
   checkout after a verified install; installed files do not depend on it.

```sh
"$HOME/.local/bin/codex-retry" disable
"$HOME/.local/bin/codex-retry" enable
"$HOME/.local/bin/codex-retry" uninstall
```

Upgrades from the earlier private macOS tool use the same state directory and
LaunchAgent label. The installer requires no pending recovery or uncertain send,
backs up the old service/config/state, switches to a versioned runtime and checks
fresh health. It restores the old service on failure without rewinding dispatch
deduplication state. Never resolve a pending send by deleting state.

Installation health and read-only compatibility are not a full recovery test.
An optional actual same-thread test needs a human-authorized idle test conversation
and `docs/VERIFICATION.md`; don't inject errors into real logs or force capacity.
