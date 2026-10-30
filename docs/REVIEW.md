# Initial release review

Two independent review axes, following the requested `code-review` workflow.
Scope: the entire project against empty `review-baseline`
(`4da1de2c929b362e843e377deab2ad90640c076e`), through `ce7de2f`.
Standards sources: AGENTS.md, CONTRIBUTING.md and docs/SAFETY.md.
Spec source: docs/SPEC.md. Review used synthetic fixtures, not real service calls.

## Standards

Three findings resolved; no remaining actionable standard violations or smell findings identified:

- Late duplicate errors could resurrect cancelled retries. Both lifecycle folding
  and event processing now preserve a cancellation barrier until a distinct new turn.
- Clean-clone verification needed a missing local directory. It now uses a shared
  private lock, serialized across checkouts and released with a context manager.
- Installation could overwrite a concurrent explicit pause. Installation and
  mutating controls now share a lock, reread preferences and validate snapshots.

The final configuration refresh and queued-thread exclusion guard also preserve
current recovery policy. 76 unit tests and `git diff --check` pass locally.

**Standards: 3 resolved, 0 remaining; no outstanding worst issue identified.**

## Spec

Four findings resolved; no remaining material spec findings identified:

- Cancellation reversal conflicted with preserving cancellation.
- The missing verification lock directory conflicted with reproducible clean-clone testing.
- An upgrade rollback could revive a concurrently uninstalled service. Mutating
  commands now share the install lock and reject contention before making changes.
- A newly excluded thread could still dispatch an existing job. Exclusions and
  current configuration are now checked before the final send.

Standalone structure, English and Chinese READMEs, simple installation, standard
library runtime, licensing, migration and rollback match the release spec. No
material scope expansion identified. 76 tests pass. Publication and actual second
Mac installation/continuation require separate delivery evidence.

**Spec: 4 resolved, 0 remaining material findings; no outstanding worst issue identified.**

This review is evidence for this revision, not a guarantee against future bugs or
Desktop API changes. Real machine receipts remain private; no session data is published.
