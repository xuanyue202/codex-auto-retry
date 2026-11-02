# Changelog

## 0.1.3

- Load an existing local thread through the public macOS thread link when owner discovery fails, once per failure and persisted before the action.
- Recheck cancellation, Goals and retry limits before loading and before sending. Loading consumes no provider attempt and does not extend the outage deadline.
- Add an explicit `retry` request for a current capacity failure that has never been dispatched. The installed daemon validates and handles it; duplicate, changed and uncertain requests are rejected.
- Keep owner activation and manual-request receipts separate from observed continuation and actual work.

## 0.1.2

- Send a fixed continuation instruction instead of an empty input array, so the
  resumed model has an explicit request to continue unfinished work.
- Persist a unique recovery ID before sending and attach it to the turn origin.
  Matching automated input preserves retry counts and the outage deadline;
  unrelated input and turns remain cancellation/supersession signals.
- Match the observed turn origin in the verifier and test the pending action,
  rather than treating a new turn alone as completed useful work.

## 0.1.1

- Verification no longer reports an empty assistant final message as successful
  output. It distinguishes a same-thread start from an observed model response.
- Nonempty assistant text or a model tool call is required for output evidence;
  tool results and empty/whitespace messages do not count.
- Modern composer/app-tool requests after dispatch make a probe inconclusive.
- Recovery/IPC behavior is unchanged. This fixes acceptance reporting and adds
  regression coverage, rather than sending extra continuations to obtain a pass.

## 0.1.0

Initial public macOS release: bounded capacity recovery, one-sentence installation,
versioned per-user service, migration/rollback, bilingual documentation and CI.
