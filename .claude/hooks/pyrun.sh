#!/bin/sh
# pyrun [--isolated] <script.py> [args]: run Python in the project env (pixi>uv/.venv);
# --isolated skips it (uv>pixi>system, for stdlib hooks). Override: PHASERUN_PY_RUNNER.
have() { command -v "$1" >/dev/null 2>&1; }
ISO=
if [ "$1" = "--isolated" ]; then ISO=1; shift; fi
if [ -z "$ISO" ]; then
  if [ -n "${PHASERUN_PY_RUNNER:-}" ]; then exec $PHASERUN_PY_RUNNER python "$@"; fi
  if { [ -f pixi.toml ] || [ -f pixi.lock ] || [ -d .pixi ]; } && have pixi; then exec pixi run python "$@"; fi
  if { [ -f uv.lock ] || [ -d .venv ]; } && have uv; then exec uv run python "$@"; fi
fi
if have uv; then exec uv run --no-project python "$@"; fi
if have pixi; then exec pixi exec -- python "$@"; fi
if have python3; then exec python3 "$@"; fi
exec python "$@"
