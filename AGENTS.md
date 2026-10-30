# Project guidance

Personal, standalone open-source project. Do not load unrelated workspace context.
Read CONTRIBUTING.md, docs/SPEC.md and docs/SAFETY.md before implementation.
Run `python3 -m unittest discover -s tests -v` and `git diff --check`.
Use synthetic fixtures only; keep private verification artifacts in ignored `private/`.
Do not operate real Desktop conversations or LaunchAgents as a test unless the
human task explicitly authorizes that operation. Installation requests authorize
the narrowly scoped workflow in INSTALL.md. No global Codex configuration edits.
