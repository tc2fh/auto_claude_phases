@echo off
setlocal enabledelayedexpansion
REM pyrun [--isolated] <script.py> [args]: run Python in the project env (pixi>uv/.venv);
REM --isolated skips it (uv>pixi>system, for stdlib hooks). Override: PHASERUN_PY_RUNNER.
set "ISO="
set "ARGS=%*"
if "%~1"=="--isolated" ( set "ISO=1" & set "ARGS=!ARGS:*--isolated =!" )
set "RUN="
if not defined ISO if defined PHASERUN_PY_RUNNER set "RUN=%PHASERUN_PY_RUNNER% python"
if not defined ISO if not defined RUN if exist "pixi.toml" call :pick pixi "pixi run python"
if not defined ISO if not defined RUN if exist "pixi.lock" call :pick pixi "pixi run python"
if not defined ISO if not defined RUN if exist ".pixi" call :pick pixi "pixi run python"
if not defined ISO if not defined RUN if exist "uv.lock" call :pick uv "uv run python"
if not defined ISO if not defined RUN if exist ".venv" call :pick uv "uv run python"
if not defined RUN call :pick uv "uv run --no-project python"
if not defined RUN call :pick pixi "pixi exec -- python"
if not defined RUN call :pick python3 "python3"
if not defined RUN set "RUN=python"
%RUN% !ARGS!
exit /b !errorlevel!

:pick
where %1 >nul 2>nul && set "RUN=%~2"
exit /b
