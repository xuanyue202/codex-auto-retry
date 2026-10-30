# Codex Auto Retry · macOS

**Step away. Let Codex pick up where it stopped.**

[![Tests](https://github.com/xuanyue202/codex-auto-retry/actions/workflows/test.yml/badge.svg)](https://github.com/xuanyue202/codex-auto-retry/actions/workflows/test.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-black.svg)](#requirements)
[English](README.md) · [简体中文](README.zh-CN.md)

You leave Codex working. You return to **“Selected model is at capacity.”**
The task has been waiting for you to press retry.

This small background helper watches for that specific failure and asks the
**same Codex Desktop conversation** to continue. Your model, workspace and
permissions stay with the conversation. No API key, subscription or Python
packages to add.

```text
Capacity failure → wait 1s → continue in the same conversation
Fails again?      → wait 2s → 4s → 8s → 16s → stop at the limit
You type or stop? → pending retry cancelled
```

*Behavior illustration. Backoff starts at detection; polling and Desktop routing
add latency. This does not bypass provider capacity or guarantee recovery.*

## Install in one sentence

Paste this into **Codex on the Mac where you want it installed**:

```text
Install Codex Auto Retry by reading and following https://raw.githubusercontent.com/xuanyue202/codex-auto-retry/v0.1.0/INSTALL.md
```

Your agent checks compatibility, installs the user background service and verifies
its health. Keep the Codex desktop app running. Installation does not submit a
model request or replay your failed tasks.

Prefer Terminal? With [requirements](#requirements) met:

```sh
curl -fsSL https://raw.githubusercontent.com/xuanyue202/codex-auto-retry/v0.1.0/install.sh | sh
```

This runs a version-pinned installer. To inspect before executing, download it or
[read the source](install.sh). It needs neither `sudo` nor shell profile changes.

## Why use it?

| When this happens | What the helper does |
| --- | --- |
| A long task ends on a structured capacity error | Continues that conversation after a short backoff |
| The same error repeats | Tries at most five times over 15 minutes |
| You send another message or cancel | Drops the pending retry |
| A conversation spans days or compacts into new files | Checks all live lifecycle shards |
| Desktop accepts a request but its outcome is uncertain | Observes for a new turn; does not blindly send twice |
| You want it off | One command pauses it; another removes the service |

Works with ordinary conversations and active native Goals. It never reactivates a
paused/completed Goal or resets its budget. A new ordinary human request after an
old Goal is handled separately.

## Stay in control

```sh
"$HOME/.local/bin/codex-retry" status      # enabled, process, heartbeat, pending work
"$HOME/.local/bin/codex-retry" doctor      # read-only Desktop compatibility check
"$HOME/.local/bin/codex-retry" disable     # pause; survives app/OS restart
"$HOME/.local/bin/codex-retry" enable      # watch future failures; no historical replay
"$HOME/.local/bin/codex-retry" stop        # pause and unload the service
"$HOME/.local/bin/codex-retry" start       # load it; preserve enabled/disabled preference
"$HOME/.local/bin/codex-retry" uninstall   # remove service and launcher; retain private data
```

If `~/.local/bin` is already on your PATH, `codex-retry status` works too.

The service starts when you log in. It watches eligible conversations owned by
your local Codex Desktop app, across workspaces. To upgrade, rerun the installer
for the desired release tag. Existing preferences, cursors and deduplication are
preserved, including migration from the earlier private macOS version. A pending
recovery blocks an upgrade until its outcome is reconciled. [Install details](INSTALL.md).

## Requirements

- macOS with a logged-in GUI session; Python **3.10+**, git and curl.
- Codex **desktop app**, running and owning the conversation on that same Mac.
- Access to its local rollout files and IPC socket (defaults under `~/.codex`;
  `CODEX_HOME` is supported on first install).

This is a macOS helper, not a Codex CLI plugin. A task running on another Mac needs
the helper on **that Mac**. The IPC interface is internal and can change with app
updates. `doctor --thread-id <id>` checks a particular owner without starting a turn.

## Privacy and limits

The helper runs locally and sends no telemetry. It transiently reads lifecycle
records, discards conversation/tool text, and stores only private IDs, paths,
timestamps, counters, result categories and Goal revision hashes. No prompts,
credentials or raw model responses are retained. See [safety and data flow](docs/SAFETY.md).

- Only exact structured `server_overloaded` / `serverOverloaded` failures trigger
  recovery. Auth, quota, billing, policy and unknown errors do not.
- No model switching, prompt replay, CLI resume or global Codex config edits.
- First install and re-enable start at the current end of logs. Previous failures
  are not automatically restarted.
- An IPC acknowledgement is not proof of execution. If no start is observed in
  30 seconds, status records `start_not_observed`; it does not send again.
- Owner loss, internal API changes and the final check/start race remain possible.
  Serial IPC lookups can delay other retries. A resumed model still needs to
  reconcile any external operation whose result was uncertain.

## How it works

```text
Local rollout lifecycle → exact capacity classifier → bounded retry queue
                                      ↓
                          recheck current thread + Goal
                                      ↓
                      persist intent → Desktop owner IPC
                                      ↓
                           observe new turn or report unknown
```

Runtime is Python standard library only. Versioned files, config, state and local
receipts live in `~/Library/Application Support/CodexAutoRetryMac/`; the single
user LaunchAgent is `com.local.codex-capacity-retry`. Uninstall retains this data
so you can inspect it; remove it yourself if you no longer need it.

## Verify or contribute

```sh
git clone https://github.com/xuanyue202/codex-auto-retry.git
cd codex-auto-retry
python3 -m unittest discover -s tests -v
```

CI uses synthetic logs and fake sockets; it does not make model calls. Optional
[same-thread integration testing](docs/VERIFICATION.md) uses isolated error
metadata and one real continuation. That is distinct from observing an actual
provider outage recover. [Contributing](CONTRIBUTING.md) · [Report a bug](https://github.com/xuanyue202/codex-auto-retry/issues/new/choose).

## Credits

Adapted from the recovery architecture and Desktop IPC framing in
[sybxxx/codex-auto-retry](https://github.com/sybxxx/codex-auto-retry), with macOS
service management and additional lifecycle/recovery guards. [MIT](LICENSE);
[upstream notice](LICENSE.upstream). Independent project, not affiliated with OpenAI.
