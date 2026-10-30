#!/bin/sh
# Version-pinned bootstrap. No sudo, shell profile edits or global Codex config.
set -eu
if [ "$(uname -s)" != Darwin ]; then
    echo 'Codex Auto Retry requires macOS.' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Install Python 3.10+ and retry. No system package changes were made.' >&2
    exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo 'Python 3.10+ is required.' >&2
    exit 1
}
if ! command -v git >/dev/null 2>&1; then
    echo 'Install git, then retry.' >&2
    exit 1
fi
retry_stage=$(mktemp -d "${TMPDIR:-/tmp}/codex-auto-retry.XXXXXXXX")
trap 'rm -rf "$retry_stage"' EXIT HUP INT TERM
git clone --quiet --depth 1 --branch v0.1.0 https://github.com/xuanyue202/codex-auto-retry.git "$retry_stage/source"
python3 "$retry_stage/source/install.py" "$@"
echo 'Ready. Run: "$HOME/.local/bin/codex-retry" status'
