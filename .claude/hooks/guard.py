#!/usr/bin/env python3
"""PreToolUse guard (Bash|PowerShell|Edit|Write|NotebookEdit): exit 2 BLOCKS the call.
Always: headless/API-billed Claude - the run must stay on the interactive subscription pool.
Only while a phase run is active (.phaserun/current_phase exists): history-destroying git,
recursive deletes of protected paths, and edits under .claude/ (no self-modification of
hooks/policy). The gate reviews after the fact; this blocks the irreversible up front."""
import json, os, re, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
tool = data.get("tool_name", "")
tool_input = data.get("tool_input") or {}
active = os.path.exists(".phaserun/current_phase")


def block(why):
    sys.stderr.write("BLOCKED (" + why + "). Stop and report this to the user; do not work around the guard.\n")
    sys.exit(2)


if tool in ("Edit", "Write", "NotebookEdit"):
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    if active and path:
        rel = os.path.relpath(os.path.abspath(path), os.getcwd()).replace("\\", "/")
        if rel == ".claude" or rel.startswith(".claude/"):
            block("editing .claude/ during a phase run - hooks/policy are off limits")
    sys.exit(0)

cmd = tool_input.get("command", "")
HEADLESS = [r"\bclaude\b[^\n|&;]*\s(?:-p|--print)\b", r"\bclaude-agent-sdk\b", r"\bANTHROPIC_API_KEY="]
if any(re.search(p, cmd) for p in HEADLESS):
    block("headless/API-billed Claude is not allowed; work via a subagent in this interactive session")
if not active:
    sys.exit(0)

DESTRUCTIVE_GIT = [r"\bgit\s+push\b[^\n|&;]*\s(?:-f|--force(?:-with-lease)?)\b",
                   r"\bgit\s+reset\b[^\n|&;]*--hard\b",
                   r"\bgit\s+clean\b[^\n|&;]*\s-\w*[fdxX]"]
if any(re.search(p, cmd) for p in DESTRUCTIVE_GIT):
    block("history-destroying git during a phase run; needs explicit user say-so")

protected = [d.strip().rstrip("/") for d in os.environ.get(
    "PHASERUN_PROTECTED_DIRS", "data,results,checkpoints,outputs,artifacts").split(",") if d.strip()]
recursive_del = (re.search(r"\brm\s+(?:-[^\s]+\s+)*-[^\s]*[rR]", cmd)
                 or re.search(r"\bRemove-Item\b[^\n]*-Recurse", cmd, re.I))
bad_target = (r"(?:^|\s)[\"']?(?:/|~/?|\$HOME/?)[\"']?(?:\s|$)"
              r"|(?:^|[\s\"'=])(?:\./)?\.git(?:[/\s\"']|$)"
              r"|(?:^|[\s\"'=])(?:\./)?(?:" + "|".join(map(re.escape, protected)) + r")(?:[/\s\"']|$)")
if recursive_del and re.search(bad_target, cmd, re.I):
    block("recursive delete touching a protected path (root, ~, .git, " + ", ".join(protected) + ") during a phase run")
sys.exit(0)
