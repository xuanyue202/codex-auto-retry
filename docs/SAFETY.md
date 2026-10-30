# Safety and data flow

## What can cause a continuation?

Only a new `event_msg.task_complete` with an exact structured `server_overloaded`
or `serverOverloaded` code. A quoted error in user/assistant/tool text never counts.
Before the single mutating request, all live shards must still show that failed
turn as terminal. New input, an active/new turn, cancellation, a changed/stopped
Goal, attempt/time limit or disabled preference cancels dispatch.

Active native Goals are eligible; paused/completed/budget-limited/unknown Goals
are not reactivated. An explicit composer request after an old stopped Goal is
an ordinary request with its own bounded retry chain. Usage-only Goal updates
do not change the semantic Goal hash. The helper never writes Goal state.

## What does it read, store and send?

- Reads local rollout files transiently; non-lifecycle records are filtered before
  JSON decoding. Lifecycle payloads can contain text which is discarded immediately.
- Stores private IDs, paths, timestamps, counters, error/result categories and
  semantic Goal hashes in a user-private directory. Hashes are not encryption;
  keep this directory private. No prompts, tool bodies, credentials or raw model
  responses are retained. Receipts can contain machine paths and must stay local.
- Sends initialization, owner-discovery and settings-read IPC requests, followed
  only when eligible by a fixed continuation instruction with inherited thread settings.
  This automated message is visible in Codex conversation history; it does not replay
  the original user prompt. A unique recovery UUID is persisted before dispatch and
  sent as client message ID and turn-origin marker. Only a matching origin and the
  exact fixed instruction can be treated as the helper's own initial input.
  Human input after a failure still cancels, even if it copies the fixed text.
- Uses the local Unix socket and existing provider routing. No telemetry endpoint,
  credentials access, API keys, shell profile changes or global Codex config writes.
  GitHub is contacted during the explicit source download/update only.

## Preventing duplicate work

The helper's own initial input does not reset the attempt count or outage deadline.
A new user request still gets its own retry budget. Observing an unrelated new turn
is recorded as superseding the request, rather than confirmation of our delivery.

Dispatch intent and failed-turn deduplication are fsynced before sending. If a
send disconnects, times out or the process dies in the ACK window, its outcome is
unknown. The helper observes a new start or reports `start_not_observed`; it never
blindly sends that failed turn again. An observed start is not proof the model
finished its work. A resumed model must reconcile uncertain external side effects.

This is not an exactly-once guarantee. The internal Desktop bridge lacks atomic
compare-and-start. A user/native controller could start a turn between the final
check and IPC mutation. Owner loss or app upgrades can break routing. Individual
IPC requests have a 15-second absolute deadline; serial dispatch can delay other
threads. Only recoverable capacity is automatically handled.

## Installation, update and removal

`install.py` performs read-only compatibility checks, stages a versioned runtime,
requires an idle dispatch state, stops only the owned LaunchAgent, backs up config,
state and plist, then switches and verifies process/heartbeat. Failure restores
the previous service without rewinding live state/seen. Unknown state versions or
invalid configuration fail closed. Existing enable preference and configuration
bytes remain intact on successful upgrade. First install begins at current EOF.
Installation and preference/service changes use the same private nonblocking
lock. Concurrent mutating commands report busy before writing; status and doctor
remain read-only. A rollback cannot revive a successfully concurrent uninstall
or overwrite a pause accepted through these controls.

The LaunchAgent runs as the logged-in user and starts on login. No privilege
escalation. It covers this local Desktop user's eligible conversations across
workspaces; users should understand that scope before enabling it. `uninstall`
disables and removes only its LaunchAgent and managed launcher; private state,
backups and versioned files remain for inspection. It never deletes Codex history.

## Reports

Use a synthetic reproducer plus version/OS/Python and sanitized result categories.
Never upload `~/.codex`, state, raw logs, local receipts or personal/business text.
For a security-sensitive issue, report the category without secrets first through
the public tracker; arrange private disclosure before sharing sensitive detail.
