# Changelog

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
