# Phase NN — <title>   (template; one file per phase: phase_01_*.md, phase_02_*.md, …)

## Goal
The bite-sized objective, 1-2 sentences.

## Scope
Files/dirs this phase may touch — the gate parses the backticked paths below; anything outside trips
out-of-scope (infra + env/build/cache dirs auto-ignored). Don't list data or results dirs unless this phase writes them.
- `src/example/`
- `tests/example/`

## Verify  (command the gate runs; activation-independent; chain build+test with `&&`; `skip` if nothing to run)
`uv run pytest -q tests/example`
<!-- C++: cmake --build build && ctest --test-dir build --output-on-failure · Rust: cargo test · pixi: pixi run pytest -q -->

## Steps
- Granular work doable in one fresh subagent session.

## Definition of done
- Observable criteria, incl. the `## Verify` command passing.

## Out of scope
- Anything in later phases.
