# Codex Auto Retry for macOS

[![Tests](https://github.com/xuanyue202/codex-auto-retry/actions/workflows/test.yml/badge.svg)](https://github.com/xuanyue202/codex-auto-retry/actions/workflows/test.yml)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[English](README.md) · [简体中文](README.zh-CN.md)

Automatically retries model-capacity failures in the same Codex Desktop conversation, preserving its model, workspace and permissions.

When a task stops with “Selected model is at capacity,” the helper waits briefly and requests a continuation. It runs as a macOS user background service and uses only the Python standard library.

## Requirements

- macOS with a logged-in desktop session, Python 3.10+, git and curl.
- Codex Desktop running on the same Mac and owning the target conversation. The command-line client alone is not sufficient.
- Access to local rollout files and the IPC socket, normally under `~/.codex`. First installation respects `CODEX_HOME`.

## Installation

Ask Codex on the target Mac to install it:

```text
Install Codex Auto Retry by reading and following https://raw.githubusercontent.com/xuanyue202/codex-auto-retry/v0.1.1/INSTALL.md
```

Or run the versioned installer in Terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/xuanyue202/codex-auto-retry/v0.1.1/install.sh | sh
```

You can [inspect the script](install.sh) before running it. Installation checks the Desktop connection, installs a user LaunchAgent and verifies its process and heartbeat. It requires no sudo, API key or shell profile changes. Installation does not send a model request.

## Retry behavior

- Only structured `server_overloaded` or `serverOverloaded` errors trigger a retry. Authentication, quota, billing, policy and unknown errors are left for the user to resolve.
- Successive failures use 1, 2, 4, 8 and 16 seconds of backoff, with at most five attempts in 15 minutes. Logs are checked once per second; scanning and Desktop requests add latency.
- New user input, cancellation or a new active turn cancels the pending retry. Active native Goals are eligible; paused or completed Goals are not reactivated and their budgets are not reset.
- The helper checks live logs across dates and context-compaction files. First installation and re-enabling start at the current end of logs, so old failures are not replayed.
- Dispatch intent is saved before sending. If acceptance is uncertain, the helper observes for a new turn rather than sending the same continuation again. A start not observed within 30 seconds is recorded as `start_not_observed`.

The helper uses the existing Desktop owner, with no model switching, original-prompt replay or CLI resume. [Implementation and safety details](docs/SAFETY.md).

## Commands

```sh
"$HOME/.local/bin/codex-retry" status      # service, heartbeat and pending retries
"$HOME/.local/bin/codex-retry" doctor      # read-only Desktop connection check
"$HOME/.local/bin/codex-retry" disable     # pause automatic retries
"$HOME/.local/bin/codex-retry" enable      # watch future failures
"$HOME/.local/bin/codex-retry" stop        # pause and unload the service
"$HOME/.local/bin/codex-retry" start       # load the service; keep the enable preference
"$HOME/.local/bin/codex-retry" uninstall   # remove the service and launcher
```

If `~/.local/bin` is on your PATH, you can use `codex-retry` directly. The service starts on login and covers eligible conversations owned by this Mac's Codex Desktop app, across workspaces. A task running on another Mac needs a separate installation there.

To update, run the installer for the release you want. Updates preserve configuration, log offsets and dispatch deduplication, including migration from the earlier private macOS build. Pending or uncertain dispatches must be resolved before updating. The installer backs up the existing service and restores it if the new service fails its health check. [Installation details](INSTALL.md).

## Limitations and test results

The helper uses an internal Desktop IPC interface, which can change between app releases. An available conversation in the task API does not necessarily have a Desktop owner. `doctor --thread-id <id>` checks a specific owner without starting a turn.

In controlled tests, one Mac continued the conversation and produced model output. A second Mac started a new turn with the same settings but returned an empty assistant message and did not execute the pending test action. Useful continuation on that machine remains unverified. Version 0.1.1 reports this as `same_thread_started_without_assistant_output`.

These tests inject error metadata into an isolated mirror and make one real continuation request. They do not establish recovery from an actual provider overload. A new turn or model response also does not establish that the task finished. [Verification procedure](docs/VERIFICATION.md).

The Desktop interface has no atomic check-and-start operation. Cancellation can race with dispatch, and serial IPC lookups can delay other threads. A resumed model still needs to reconcile external actions whose outcomes are uncertain.

## Privacy and local files

The helper sends no telemetry. It reads lifecycle records locally, discards conversation and tool text, and retains IDs, paths, timestamps, counters, result categories and Goal revision hashes. It does not retain prompts, credentials or raw model responses.

Runtime versions, configuration, state and receipts are stored in `~/Library/Application Support/CodexAutoRetryMac/`. The user LaunchAgent is `com.local.codex-capacity-retry`. Uninstall removes the service and launcher but retains local data for inspection. It does not delete Codex history or change global Codex settings.

## Development

```sh
git clone https://github.com/xuanyue202/codex-auto-retry.git
cd codex-auto-retry
python3 -m unittest discover -s tests -v
```

CI uses synthetic logs and fake sockets without model calls. See [Contributing](CONTRIBUTING.md), [release review](docs/REVIEW.md) and the [issue tracker](https://github.com/xuanyue202/codex-auto-retry/issues).

## License and attribution

MIT licensed. Recovery architecture and Desktop IPC framing are adapted from [sybxxx/codex-auto-retry](https://github.com/sybxxx/codex-auto-retry); its copyright notice is retained in [LICENSE.upstream](LICENSE.upstream). This project is independent of OpenAI.
