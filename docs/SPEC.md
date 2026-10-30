# Open-source release specification

Source: the maintainer's request to turn the working personal macOS tool into an
independent public GitHub project, then review the whole result and migrate a
second Mac using the public instructions.

- Standalone repository, outside the original notes workspace. No private history,
  real receipts, session IDs, machine paths or credentials in published files.
- Git author and committer: `xuanyue202 <yaoliang.ch@gmail.com>` for this personal project.
- Preserve proven bounded same-Desktop-thread recovery for exact structured
  capacity errors: no prompt replay, model switch, CLI resume, historical replay,
  Goal mutation or blind resend after uncertain acceptance. Preserve cancellation,
  compaction handling, manual-request budgets and restart guards.
- Separate lifecycle parsing, IPC transport, recovery policy and installation
  responsibilities without adding a dependency framework or speculative platform support.
- Default English README and a separate Chinese README. Lead with concrete value
  and immediate installation; show honest behavior and documented constraints.
- Offer a one-sentence install request for Codex plus a one-command terminal
  installer. Require macOS/Python 3.10+, use no sudo or new accounts/keys, and
  perform read-only compatibility checks. Clearly distinguish installation from
  full same-thread continuation verification.
- Install a user LaunchAgent with login startup; provide status, doctor, enable,
  disable, stop, start, uninstall, and a documented version-pinned update path.
- Safely migrate the previous private runtime in place, preserving configuration,
  cursors, retries and deduplication. Reject pending recovery during upgrade.
  Stage versioned runtimes, back up and roll back service changes on failed health.
- Provide automated regression tests/CI, MIT licensing/upstream attribution,
  contribution and issue guidance, privacy documentation and reproducible testing.
- Review the entire project using Standards and Spec parallel reviews against
  the empty `review-baseline` commit; fix material findings before publication.
- Publish the GitHub repository and a pinned release, then ask the already
  authorized Mac Local task to install from the public repository and validate
  migration, fresh service health and a bounded same-thread integration check.
  Record private evidence locally and report any unverified boundary honestly.

## Empty-output repair

The maintainer requested repair of the second Mac's empty automatic continuation
and complete verification that work resumes. A valid repair must give the model
an execution request without replaying original prompts, changing inherited
settings or repeating uncertain external actions. Automated input must preserve
retry/time limits and cancellation. Acceptance requires the automatic turn to
execute a guarded pending action with the expected identity; new-turn metadata
alone is insufficient. Keep real test evidence private.
