<!-- .claude/workflow.md — phase-orchestration policy, loaded on request by CLAUDE.md. -->

# Phase Orchestration Workflow

Run a multi-phase build entirely in THIS interactive session (bills the subscription pool).
Triggers: "start the phase run", "run the next phase", "orchestrate the plan".
**Never use headless `claude -p` / Agent SDK** — a hook blocks it; if blocked, stop and tell me.

You are the **orchestrator**: don't write phase code yourself. Per phase, delegate to a fresh
**executor** subagent, then a **summarizer** subagent. Keep messages terse; keep state on disk:
- plan `plan_docs/phase_*.md` · log `PROGRESS.md` · verdict `.phaserun/verdict_<phase>.json`
- gate result `.phaserun/gate_result.json` (written by the SubagentStop hook)

## Setup (first run)
- Need a git repo + baseline commit (`git rev-parse HEAD` must work), else stop and ask — the gate is git-based.
- Hooks run stdlib Python via `.claude/hooks/pyrun --isolated` (isolated uv/pixi/system, never your
  project env, so a broken env can't disable the gate/guard). Your code/tests run in the project env
  via `## Verify`. `settings.json` uses `pyrun.cmd`; on macOS/Linux switch to `sh .claude/hooks/pyrun.sh --isolated`.

## Verify (per phase)
Each phase file's `## Verify` section is the command the gate runs for it. Resolution:
`## Verify` → `PHASERUN_VERIFY_CMD` → autodetect (pixi/uv/cargo/cmake/pytest/make); none → STOP (no silent pass).
Use activation-independent cmds (`uv run pytest -q`, `pixi run pytest`, `cmake --build build && ctest …`),
chain build+test with `&&`, put a fast check on heavy phases (full suite at a checkpoint phase), `skip` for docs-only.

## Per-phase loop (each `plan_docs/phase_NN_*.md` in order)
1. Read only this phase file + tail of `PROGRESS.md`; don't pre-read later phases.
2. Write `.phaserun/current_phase` (this file's path) and `.phaserun/base_ref` (`git rev-parse HEAD`) —
   the gate needs them for per-phase scope/size. Spawn a fresh **executor**: implement EXACTLY this
   phase, stay in the Scope files, don't start later phases, stop when done. Pass it the phase text +
   relevant `PROGRESS.md` handoff.
3. On executor stop the hook runs the gate (writes `gate_result.json`) — trust it, not your impression.
   (Verify is cached by code fingerprint, so the summarizer's stop won't re-run it.) If verify failed,
   delegate a bounded fix (≤2 attempts, same scope), re-check; still failing → escalate, don't advance.
4. Spawn a **summarizer**: inspect the diff, write `.phaserun/verdict_<phase>.json` (schema
   `.claude/verdict.schema.json`) + a short handoff DELTA (what actually happened — decisions/surprises/
   deviations + pointer to next phase; NOT a plan restatement). Unsure a deviation is major → mark major, escalate=true.
5. Read `gate_result.json` + verdict. STOP and hand to me if ANY: verify failed (`tests_passed:false`)
   or none (`null`); diff over size limits; destructive op in added code; write under a protected data
   dir; dependency pins changed; large artifact; out-of-scope files; verdict `plan_deviation:"major"` /
   `phase_status:"blocked"` / `escalate:true`; or circuit breaker — `MAX_AUTO_PHASES` (3) auto-advances
   since I last reviewed. Objective flags come from the hook (don't override); judgment flags from the
   summarizer; stop if either fires. (Destructive/data/pin flags = review, not ban.)
6. Clean pass: append a one-paragraph `PROGRESS.md` entry (phase, status, key decisions, next-phase
   notes), `git add -A && git commit -m "<phase>: <status>"` (safe — `.gitignore` excludes envs/build/
   data, large-artifact check backstops), advance automatically. Don't ask on a clean pass.
7. Stop: 3-5 lines (phase, why, the gate reasons), then wait. After I reply, reset the breaker counter.

## Settings / env knobs (defaults in parens)
`MAX_AUTO_PHASES`=3. Gate: `PHASERUN_MAX_FILES`(15) `PHASERUN_MAX_LINES`(600) `PHASERUN_MAX_FILE_MB`(25)
`PHASERUN_PROTECTED_DIRS`(data,results,checkpoints,outputs,artifacts) `PHASERUN_IGNORE_DIRS`(extra)
`PHASERUN_VERIFY_CMD`(fallback) `PHASERUN_PY_RUNNER`(force runner, e.g. `pixi run`/`uv run`).
Models: executor=Opus, summarizer=Sonnet/Haiku (frontmatter `model:` wins). Between phases you may
`/clear` and re-read `PROGRESS.md` + the next phase file.

## Hard rules
- Interactive only; no headless/SDK (a hook blocks it). Can't do it interactively → stop and tell me.
- Hook = source of truth for objective checks. Missing/malformed `gate_result.json`, or "not a git
  repository" → escalate, don't assume pass.
- Never advance a phase whose verify is red, that left scope, or that has no verify command.
