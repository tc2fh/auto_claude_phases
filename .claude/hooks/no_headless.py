#!/usr/bin/env python3
"""PreToolUse(Bash|PowerShell) guard: exit 2 BLOCKS headless/API-billed Claude so the run stays on
the interactive subscription pool. Instructions guide; this enforces."""
import json, re, sys
try:
    cmd = (json.load(sys.stdin).get("tool_input", {}) or {}).get("command", "")
except Exception:
    sys.exit(0)
BLOCKED = [r"\bclaude\b[^\n|&;]*\s-p\b", r"\bclaude\b[^\n|&;]*--print\b",
           r"\bclaude-agent-sdk\b", r"\bANTHROPIC_API_KEY="]
if any(re.search(p, cmd) for p in BLOCKED):
    sys.stderr.write("BLOCKED: headless/API-billed Claude is not allowed; this run must stay in the "
                     "interactive session. Do the work via a subagent (Task tool) instead.\n")
    sys.exit(2)
sys.exit(0)
