# Verification levels

1. Unit tests: synthetic lifecycle logs, fake IPC, installer rollback and migration.
   No actual Desktop service changes or model requests.
2. `doctor`: real read-only IPC initialization, optionally exact thread owner/settings.
3. Installer receipt: actual running user service, versioned hashes, fresh PID/heartbeat.
4. Optional integration: isolated error metadata drives the same production Engine
   and one real continuation of the authorized test conversation.
5. Real incident recovery: an actual eligible upstream error followed by observed retry.

Each level is distinct. CI cannot prove real provider recovery. Keep receipts and
real IDs under ignored `private/`, never in issues or commits.

## One bounded same-thread test

Only run when a human asks for a real continuation test in a suitable conversation.
Use the exact installed release checkout; `verify.py` validates every installed
runtime file against its local source manifest. No need to force a provider error.

In the authorized test conversation, inspect current lifecycle metadata:

```sh
python3 verify.py --inspect
```

If `CODEX_THREAD_ID` is not supplied by the host, pass `--thread-id <test-thread-id>`.
Take the current `task_started` turn ID, then launch a detached helper with
stdout/stderr redirected into ignored `private/`. A plain shell background job
can exit when the agent's terminal command ends. Start a separate session and
check that the process survives a subsequent tool call before ending the turn:

```sh
python3 - <<'PY'
from pathlib import Path
import subprocess
import sys

Path('private').mkdir(exist_ok=True)
with Path('private/integration.log').open('ab') as log:
    process = subprocess.Popen(
        [sys.executable, str(Path('verify.py').resolve()),
         '--thread-id', '<test-thread-id>', '--after-turn', '<active-turn-id>',
         '--evidence', str(Path('private/integration.receipt.json').resolve()),
         '--synthetic-goal-status', 'active', '--timeout', '600'],
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
Path('private/integration.pid').write_text(str(process.pid))
print(process.pid)
PY
```

Replace both placeholders with the exact authorized IDs. In a separate tool
call, check that PID's command and the receipt's waiting phase with zero sends.
If the helper has exited, preserve its receipt and reconcile any dispatch before
preparing another probe. A new manual request cannot substitute for the pending
automatic turn. Use a fresh receipt and challenge for a replacement window.

The agent must write a local handoff that tells the resumed turn to read this
exact receipt first, then end its successful current turn. The helper waits for
completion, injects overload/active-Goal lifecycle events in an isolated temporary
mirror, follows detection/backoff, and sends one fixed-instruction continuation to the
real same-thread Desktop owner. It never changes actual Goal state or real logs.

On resumption, read the receipt and `.intent.json`; require `same_thread_resumed`,
one send, distinct real resumed turn with the exact persisted recovery-origin marker, assistant output, equal model/cwd/resolved
effort/full permission hash, `delivery_observation.result=new_turn_observed`,
mirror cleanup and healthy enabled daemon. Independently corroborate the real
completion/new-start metadata. Confirm the helper exited.

An empty final assistant message does not establish model output. A completed
new turn with no nonempty assistant text or model tool call records
`same_thread_started_without_assistant_output`, not `same_thread_resumed`.
This confirms routing/start only; it does not prove useful work continued. Modern
composer and app-tool messages arriving after dispatch make the probe inconclusive
instead of letting an unrelated new request stand in for the continuation.

New human input, cancellation, errors in the original turn, a non-idle real thread
or changed settings aborts the probe. If delivery is uncertain, reconcile before
any new send. Never arm another probe merely because a receipt is missing.

Synthetic active-Goal metadata does not create an actual backend native Goal.
This proves the guarded branch and real continuation, not genuine upstream
capacity recovery or an actual native Goal outage.

Pass `--synthetic-error bad_request` to exercise the terminal
`other` + `{"detail":"Bad Request"}` shape instead of overload. The same isolated
mirror, one-send, inherited-context and actual-work checks apply. Never inject
these events into production logs or replay expired business work.

## Verifying that work continued

A full acceptance check also prepares a pending action with a random nonce in
ignored `private/`. The original turn must not complete that action. Only the
first automatic turn after the original completes may write the result marker;
its turn ID, recovery origin and full model/workspace/permission context must
match the receipt. A later manual audit cannot supply the result. Record helper
exit, mirror cleanup and service health separately from the actual action.

## Unattended acceptance with the installed daemon

Use a human-authorized retained conversation. Before triggering, record a failed
owner lookup with successful IPC initialization, the exact failed turn, current
full context, and that no send intent or uncertain delivery exists. The target
page must not be opened in advance. If the conversation was opened during
diagnosis, first establish a genuinely missing owner again; do not substitute a
fake negative response or call an already-owned run unattended acceptance.

For a retained unsent real capacity failure whose automatic window expired, the
human can explicitly request `codex-retry retry --thread-id ... --turn-id ...`.
Use the installed launcher, not a second sending process. Record the request
receipt, daemon PID/runtime manifest, durable activation intent, observed owner,
single dispatch UUID, native origin, same full context, actual model/tool work
and final turn outcome. The daemon itself must perform the loading step.

An original business deadline is not reset by the explicit recovery window.
Actual continuation may correctly perform a time check and record a missed
window; that does not establish timely business execution. Preserve that result
separately from the success of same-thread recovery.

## 0.1.4 acceptance

The generic Bad Request change passed 134 synthetic tests, compilation and
diff whitespace checks. Independent Standards and Spec reviews found no remaining
actionable findings after native-retry budget/eligibility, structured-details
and legacy event-fingerprint regressions were fixed.

A human-authorized macOS upgrade preserved configuration bytes, the enable epoch,
source offsets and dispatch deduplication. All 15 installed runtime files matched
the source manifest; process and heartbeat checks passed with recovery enabled.

The isolated-mirror verifier then injected the exact generic `other` /
`{"detail":"Bad Request"}` shape and sent one real same-thread continuation.
Native recovery origin, original model, resolved effort, working directory and
full permission-context hash matched. The first automatic turn completed a
random-nonce pending action guarded against manual/original/foreign turns.
The helper exited, its temporary mirror was removed, and the service remained
healthy. Private receipts and identifiers are not published.

This is detection-to-continuation-and-work acceptance with a synthetic error,
not evidence that a genuine upstream Bad Request was forced or recovered.
It does not establish whole-day business-task completion or an exactly-once
guarantee for external actions.
