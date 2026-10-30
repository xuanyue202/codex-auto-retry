# Contributing

Python 3.10+; runtime uses only the standard library. Run from the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q codex_retry retry_watchdog.py control.py install.py
git diff --check
```

Keep changes small. Put transient lifecycle parsing in `codex_retry/lifecycle.py`,
framing/deadlines in `codex_retry/ipc.py`, recovery policy in `retry_watchdog.py`,
and installation/LaunchAgent concerns in `install.py` and `codex_retry/service.py`.
Use descriptive names and standard Python formatting; do not abstract for
unimplemented platforms. Include a failing regression for any recovery-policy
or installer behavior fix. Fake sockets and temporary directories should cover
failure paths without contacting a real model or modifying a real service.

Safety invariants are in [docs/SAFETY.md](docs/SAFETY.md). Do not weaken them to
make a flaky integration test pass. Persist intent before mutation; preserve
deduplication state on restart/upgrade/rollback. ACK is not observed execution.

Never commit real logs, state, machine-specific paths, conversation text, real
thread IDs or installation receipts. Use synthetic fixtures. Bug reports should
contain the release/version, OS/Python versions and sanitized result categories.
Prefer a small reproducer over a log dump. A Desktop API change may need a guarded
compatibility change; don't fall back to replaying the original prompt.

Open an issue for a behavior change; describe the trigger, expected result,
observed result and validation. PRs should state the effect and relevant tests.
Documentation examples must be runnable from a clean clone and claims must match
the measured verification level. Update both READMEs when user behavior changes.
