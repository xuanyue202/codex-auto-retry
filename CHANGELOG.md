# Changelog

## 0.1.4

- Recover terminal generic Bad Request failures, including `other` with the exact
  `{"detail":"Bad Request"}` error object. Detailed validation and unsupported
  error categories remain excluded; native `willRetry: true` is not intercepted.
- Use separate 2/5/15-second backoff and a three-continuation limit for this
  category, within the existing total attempt and outage limits. Counters,
  cancellation, Goal guards and uncertain-send protection survive restart.
- Add the `recover_bad_requests` opt-out and expose its effective limits in
  status. Existing installation configuration and offsets remain intact.
- Allow the isolated same-thread verifier to exercise this exact error shape;
  synthetic verification does not establish recovery from a real provider error.

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
