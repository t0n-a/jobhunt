#!/usr/bin/env bash
# Print today's coach task when a Claude Code session starts.
#
# Wire it up in .claude/settings.json:
#   {"hooks": {"SessionStart": [{"hooks": [{"type": "command",
#     "command": "bash /path/to/jobhunt/scripts/jobhunt-session-hook.sh"}]}]}}
#
# Opening Claude Code is already a habit, so this costs no new one. It never
# fails a session: any error is swallowed and prints nothing.
set -u
cd "$(dirname "$0")/.." || exit 0
uv run jobhunt today 2>/dev/null || true
