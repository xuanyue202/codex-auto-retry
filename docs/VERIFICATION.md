# Verification levels

1. Unit tests: synthetic lifecycle logs, fake IPC, installer rollback and migration.
   No actual Desktop service changes or model requests.
2. `doctor`: real read-only IPC initialization, optionally exact thread owner/settings.
3. Installer receipt: actual running user service, versioned hashes, fresh PID/heartbeat.
4. Optional integration: isolated error metadata drives the same production Engine
   and one real continuation of the authorized test conversation.
5. Real incident recovery: an actual upstream overload followed by observed retry.

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
Take the current `task_started` turn ID, then launch a background helper with
stdout/stderr redirected into ignored `private/`:

```sh
mkdir -p private
python3 verify.py --thread-id <test-thread-id> --after-turn <active-turn-id> \
  --evidence private/integration.receipt.json --synthetic-goal-status active \
  > private/integration.log 2>&1 &
```

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

## Verifying that work continued

A full acceptance check also prepares a pending action with a random nonce in
ignored `private/`. The original turn must not complete that action. Only the
first automatic turn after the original completes may write the result marker;
its turn ID, recovery origin and full model/workspace/permission context must
match the receipt. A later manual audit cannot supply the result. Record helper
exit, mirror cleanup and service health separately from the actual action.
