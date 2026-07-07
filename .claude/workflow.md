<!-- .claude/workflow.md - phase-orchestration policy, loaded on request by CLAUDE.md. -->

# Phase Orchestration Workflow

Run a multi-phase build entirely in THIS interactive session (bills the subscription pool).
Triggers: "start the phase run", "run the next phase", "orchestrate the plan".
**Never use headless `claude -p` / Agent SDK** - a hook blocks it; if blocked, stop and tell me.

You are the **orchestrator**: coordinate, never write phase code yourself. Per phase, spawn the
`phase-executor` agent, then the `phase-summarizer` agent (their standing instructions live in
`.claude/agents/`; your task message carries only phase-specific facts). State lives on disk:
- plan `plan_docs/phase_*.md` · log `PROGRESS.md` · verdicts `.phaserun/verdict_<phase>.json`
- gate result `.phaserun/gate_result.json`, written by the SubagentStop hook. The gate and the
  destructive-command guard are armed only while `.phaserun/current_phase` exists.

## Setup (first run)
- Need a git repo + baseline commit (`git rev-parse HEAD` must work), else stop and ask - the gate is git-based.
- Hooks run stdlib Python via `sh .claude/hooks/pyrun.sh --isolated` (isolated uv/pixi/system, never
  the project env, so a broken env can't disable the gate/guard). Windows: switch `settings.json` to
  `.claude\hooks\pyrun.cmd`. Your code/tests run in the project env via `## Verify`.

## Verify (per phase)
Resolution: phase file `## Verify` → `PHASERUN_VERIFY_CMD` → autodetect (pixi/uv/cargo/cmake/pytest/
make); none → STOP (no silent pass). Use activation-independent cmds (`uv run pytest -q`,
`pixi run pytest`, `cmake --build build && ctest ...`), chain build+test with `&&`, put a fast check
on heavy phases (full suite at a checkpoint phase), `skip` for docs-only.

## Per-phase loop (each `plan_docs/phase_NN_*.md` in order)
1. Read only this phase file + the tail of `PROGRESS.md`; don't pre-read later phases.
2. Write `.phaserun/current_phase` (this file's path) and `.phaserun/base_ref` (`git rev-parse HEAD`) -
   the gate needs them for per-phase scope/size. Spawn `phase-executor` with the phase file path +
   the previous phase's handoff_delta.
3. On executor stop the hook runs the gate (writes `gate_result.json`) - trust it, not your
   impression. Verify failed → delegate a bounded fix (≤2 attempts, same scope) to a fresh
   `phase-executor`; still red → stop, don't advance.
4. Spawn `phase-summarizer` with the phase file path, base_ref, and output path
   `.phaserun/verdict_<phase>.json`.
5. Read `gate_result.json` + the verdict. First confirm gate_result's `phase_file`/`base_ref` match
   this phase - missing or mismatched means the hook didn't run: escalate. STOP and hand to me if ANY:
   gate `stop_recommended:true` · verdict `plan_deviation:"major"` / `phase_status:"blocked"` /
   `escalate:true` · breaker: count in `.phaserun/auto_advances` ≥ `MAX_AUTO_PHASES` (3). Objective
   flags come from the hook - never override them. (Destructive/data/pin flags = review, not ban.)
6. Clean pass: append a one-paragraph `PROGRESS.md` entry (phase, status, key decisions, next-phase
   notes), `git add -A && git commit -m "<phase>: <status>"` (safe - `.gitignore` excludes envs/build/
   data, the large-artifact check backstops), bump `.phaserun/auto_advances`, advance. Don't ask.
7. Stop: 3-5 lines (phase, why, the gate reasons), then wait. After I reply, reset `.phaserun/auto_advances` to 0.
8. All phases done (or I abort): delete `.phaserun/current_phase` to stand the hooks down, then give
   a short run summary.

## Token thrift
- Everything needed to resume is on disk (PROGRESS.md tail + next phase file); I may `/clear`
  between phases. Keep your status updates to me at 1-3 lines.
- Never paste diffs, file bodies, or test logs into the conversation. Read `gate_result.json` and
  the verdict JSON; tail `.phaserun/verify_output.txt` only when verify failed.
- Subagent task messages: phase-specific facts only (path, base_ref, handoff) - the standing
  instructions already live in the agent definitions.

## Settings / env knobs (defaults in parens)
`MAX_AUTO_PHASES`(3). Gate: `PHASERUN_MAX_FILES`(15) `PHASERUN_MAX_LINES`(600) `PHASERUN_MAX_FILE_MB`(25)
`PHASERUN_PROTECTED_DIRS`(data,results,checkpoints,outputs,artifacts) `PHASERUN_IGNORE_DIRS`(extra)
`PHASERUN_VERIFY_CMD`(fallback) `PHASERUN_PY_RUNNER`(force runner, e.g. `pixi run`/`uv run`).
Models are pinned in agent frontmatter (executor=opus, summarizer=sonnet); edit there.

## Hard rules
- Interactive only; no headless/SDK. During a run the guard also blocks destructive git/deletes and
  edits under `.claude/`. Any hook block → stop and tell me; never work around it.
- Hook = source of truth for objective checks. Missing/malformed/stale `gate_result.json`, or "not
  a git repository" → escalate, don't assume pass.
- Never advance a phase whose verify is red, that left scope, or that has no verify command.
