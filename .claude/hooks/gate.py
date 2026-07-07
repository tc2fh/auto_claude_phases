#!/usr/bin/env python3
"""Deterministic phase gate (SubagentStop hook). No-ops unless a phase run is active
(.phaserun/current_phase exists), so ordinary sessions never trigger test runs. Runs real checks,
writes .phaserun/gate_result.json which the orchestrator treats as authoritative. Always exits 0
(reports, never blocks). Checks: verify (cached), diff size, destructive ops, data clobber,
dependency pins, large artifacts, out-of-scope. Sci-computing defaults (Python/C++/Rust/Make)."""

import hashlib, json, os, re, shutil, subprocess, sys, pathlib

ENV_VERIFY_CMD = os.environ.get("PHASERUN_VERIFY_CMD", "").strip()
MAX_FILES = int(os.environ.get("PHASERUN_MAX_FILES", "15"))
MAX_LINES = int(os.environ.get("PHASERUN_MAX_LINES", "600"))
MAX_FILE_MB = float(os.environ.get("PHASERUN_MAX_FILE_MB", "25"))
SCAN_MAX_BYTES = 2 * 1024 * 1024
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git empty-tree hash

# Generated/vendored/env dirs: never counted or scanned (independent of .gitignore). Extend: PHASERUN_IGNORE_DIRS.
IGNORE_DIRS = {".claude", ".phaserun", ".git", ".venv", "venv", "env", ".pixi", ".conda",
    "__pypackages__", ".tox", ".nox", "node_modules", "site-packages", ".eggs", "build", "dist",
    "target", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", ".ipynb_checkpoints"} | {
    d.strip() for d in os.environ.get("PHASERUN_IGNORE_DIRS", "").split(",") if d.strip()}
PROTECTED_DIRS = [d.strip().rstrip("/") for d in os.environ.get(
    "PHASERUN_PROTECTED_DIRS", "data,results,checkpoints,outputs,artifacts").split(",") if d.strip()]

DESTRUCTIVE_ALWAYS = [
    ("SQL drop/truncate/delete", r"\b(?:DROP\s+(?:TABLE|DATABASE)|TRUNCATE|DELETE\s+FROM)\b"),
    ("rm -rf", r"rm\s+-(?:rf|fr)\b"), ("rmdir /s", r"\brmdir\s+/s\b"),
    ("Remove-Item -Recurse", r"Remove-Item\b[^\n]*-Recurse"), ("shutil.rmtree", r"shutil\.rmtree\b"),
    ("git push --force", r"git\s+push\b[^\n]*--force\b"), ("--force-with-lease", r"--force-with-lease\b"),
    ("git lfs --force/prune", r"git\s+lfs\b[^\n]*(?:--force\b|\bprune\b)"), ("dvc destroy/gc", r"\bdvc\s+(?:destroy|gc)\b"),
]
DATA_WRITE = [
    ("to_parquet/to_csv/to_hdf/to_feather", r"\.to_(?:parquet|csv|hdf|feather)\("),
    ("np.save/savez", r"\b(?:np|numpy)\.savez?\b"), ("torch.save", r"\btorch\.save\("),
    ("h5py write-mode", r"h5py\.File\([^)]*['\"][wa]['\"]"),
]
PINFILES = {"poetry.lock", "uv.lock", "pixi.lock", "pdm.lock", "Pipfile.lock", "conda-lock.yml",
    "Cargo.lock", "vcpkg.json", "conanfile.txt", "environment.yml", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "Gemfile.lock", "composer.lock"}
PINFILE_PATTERNS = [r"^requirements[\w.-]*\.txt$"]
SCAN_EXTS = {".py", ".pyx", ".ipynb", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx", ".rs",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd", ".cmake", ".make", ".mk", ".toml", ".cfg",
    ".ini", ".yml", ".yaml", ".json", ".jl", ".f", ".f90", ".f95", ".cu", ".cuh", ".txt", ".r", ".m"}
SCAN_NAMES = {"Makefile", "makefile", "GNUmakefile", "CMakeLists.txt", "meson.build", "Dockerfile"}

STATE = pathlib.Path(".phaserun")


def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def in_ignored_dir(path):
    return any(p in IGNORE_DIRS or p.endswith(".egg-info") for p in path.replace("\\", "/").split("/") if p)


def have(tool):
    return shutil.which(tool) is not None


def _scannable(p):
    return p.suffix.lower() in SCAN_EXTS or p.name in SCAN_NAMES


def _read(path):
    return pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")


def _section_body(text, keyword):
    # Body under the first heading STARTING with <keyword> (so 'out of scope' won't match 'scope').
    head_re = re.compile(r"^\s{0,3}#{1,6}\s+" + re.escape(keyword) + r"\b", re.I)
    any_head = re.compile(r"^\s{0,3}#{1,6}\s")
    out, capturing = [], False
    for line in text.splitlines():
        if any_head.match(line):
            if capturing:
                break
            if head_re.match(line):
                capturing = True
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def current_phase_file():
    p = pathlib.Path(".phaserun/current_phase")
    if p.exists():
        f = pathlib.Path(_read(p).strip())
        if f.exists():
            return f
    phases = sorted(pathlib.Path("plan_docs").glob("phase_*.md"))
    return phases[0] if phases else None


def phase_verify_cmd(phase_file):
    if not phase_file or not phase_file.exists():
        return None
    body = _section_body(_read(phase_file), "verify")
    if not body:
        return None
    fenced = re.search(r"```[\w-]*\n(.*?)```", body, re.S)
    if fenced:
        cand = fenced.group(1).strip()
    else:
        cand = ""
        for ln in body.splitlines():
            ln = ln.strip().lstrip("-*").strip().strip("`").strip()
            if ln and not ln.startswith("<!--"):
                cand = ln
                break
    return "skip" if cand.lower() in {"skip", "none", "(none)", "n/a"} else (cand or None)


def autodetect_verify():
    # Default verify when a phase declares none; Python routes through the project's pixi/uv env.
    forced = os.environ.get("PHASERUN_PY_RUNNER", "").strip()
    if forced:
        return forced + " pytest -q"
    cwd = pathlib.Path(".")
    def has(*names):
        return any((cwd / n).exists() for n in names)
    pyproject = _read(cwd / "pyproject.toml") if (cwd / "pyproject.toml").exists() else ""
    if (has("pixi.toml", "pixi.lock", ".pixi") or "[tool.pixi]" in pyproject) and have("pixi"):
        return "pixi run pytest -q"
    if (has("uv.lock", ".venv") or "[tool.uv]" in pyproject) and have("uv"):
        return "uv run pytest -q"
    if has("Cargo.toml"):
        return "cargo test"
    if (cwd / "CMakeLists.txt").exists() and (cwd / "build").exists():
        return "cmake --build build && ctest --test-dir build --output-on-failure"
    if has("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg") or (cwd / "tests").exists():
        return "pytest -q"
    for mk in ("Makefile", "makefile", "GNUmakefile"):
        p = cwd / mk
        if p.exists() and re.search(r"(?m)^test\s*:", _read(p)):
            return "make test"
    return "meson test -C build" if (cwd / "meson.build").exists() else None


def resolve_verify(phase_file):
    pv = phase_verify_cmd(phase_file)
    if pv:
        return pv, "phase"
    if ENV_VERIFY_CMD:
        return ENV_VERIFY_CMD, "env"
    auto = autodetect_verify()
    return (auto, "auto") if auto else (None, "none")


def declared_scope(phase_file):
    if not phase_file or not phase_file.exists():
        return None
    body = _section_body(_read(phase_file), "scope")
    if not body:
        return None
    globs = re.findall(r"`([^`]+)`", body) or re.findall(r"[\w./*-]+/|[\w./*-]+\.\w+", body)
    globs = [g.strip().strip("`") for g in globs if g.strip()]
    return globs or None


def base_ref():
    p = pathlib.Path(".phaserun/base_ref")
    if p.exists() and _read(p).strip():
        return _read(p).strip()
    return git("rev-parse", "--verify", "-q", "HEAD~1").strip() or EMPTY_TREE


def changed_files(base):
    raw = (git("diff", "--name-only", base, "HEAD").splitlines() + git("diff", "--name-only").splitlines()
           + git("diff", "--name-only", "--cached").splitlines())
    for row in git("status", "--porcelain").splitlines():
        if not row.strip():
            continue
        path = row[3:].strip().strip('"').split(" -> ")[-1]
        if row.startswith("??"):
            if in_ignored_dir(path):
                continue
            entry = pathlib.Path(path)
            if entry.is_dir():
                raw += [str(p).replace("\\", "/") for p in entry.rglob("*")
                        if p.is_file() and not in_ignored_dir(str(p).replace("\\", "/"))]
            else:
                raw.append(path)
        else:
            raw.append(path)
    return sorted({f.replace("\\", "/") for f in raw if f and not in_ignored_dir(f)})


def changed_lines(base):
    # Single base-vs-worktree diff: it already covers committed+staged+unstaged; adding
    # `git diff --numstat` on top would double-count unstaged edits.
    n = 0
    for row in git("diff", "--numstat", base).splitlines():
        parts = row.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit() and not in_ignored_dir(parts[2]):
            n += int(parts[0]) + int(parts[1])
    return n


def _added_lines_by_path(diff_text):
    # Only '+' content lines, attributed to their file, skipping ignored dirs and diff headers.
    cur, out = None, []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            cur = p[2:] if p.startswith("b/") else p
        elif line.startswith(("--- ", "diff ", "@@", "index ", "new file", "deleted file", "rename ")):
            continue
        elif line.startswith("+") and (cur is None or not in_ignored_dir(cur)):
            out.append(line[1:])
    return out


def added_content(base):
    chunks = []
    for diff in (git("diff", base, "HEAD"), git("diff"), git("diff", "--cached")):
        chunks += _added_lines_by_path(diff)
    for row in git("status", "--porcelain").splitlines():
        if not row.startswith("??"):
            continue
        path = row[3:].strip().strip('"')
        if in_ignored_dir(path):
            continue
        entry = pathlib.Path(path)
        for p in ([entry] if entry.is_file() else [q for q in entry.rglob("*") if q.is_file()]):
            if in_ignored_dir(str(p).replace("\\", "/")) or not _scannable(p):
                continue
            try:
                if p.stat().st_size <= SCAN_MAX_BYTES:
                    chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass
    return "\n".join(chunks)


def evaluate_destructive(added, changed):
    hits = [label for label, pat in DESTRUCTIVE_ALWAYS if re.search(pat, added, re.I | re.M)]
    if PROTECTED_DIRS:
        protre = re.compile(r"(?:^|[\"'/\s])(?:" + "|".join(re.escape(d) for d in PROTECTED_DIRS) + r")/", re.I)
        for label, pat in DATA_WRITE:
            for m in re.finditer(pat, added, re.I):
                ls, le = added.rfind("\n", 0, m.start()) + 1, added.find("\n", m.end())
                if protre.search(added[ls: le if le != -1 else len(added)]):
                    hits.append("write under protected dir (" + label + ")")
                    break
    mig = [f for f in changed if re.search(r"(?:^|/)(?:migrations|alembic/versions)/", f)]
    if mig:
        hits.append("schema migration files: " + ", ".join(mig[:3]))
    return sorted(set(hits))


def evaluate_pins(changed):
    return sorted({os.path.basename(f) for f in changed if os.path.basename(f) in PINFILES
                   or any(re.search(p, os.path.basename(f)) for p in PINFILE_PATTERNS)})


def evaluate_large(changed):
    big, cap = [], MAX_FILE_MB * 1024 * 1024
    for f in changed:
        try:
            sz = pathlib.Path(f).stat().st_size
        except Exception:
            continue
        if sz > cap:
            big.append("%s (%.0f MB)" % (f, sz / 1024 / 1024))
    return big


def evaluate_scope(scope, changed):
    if scope is None:
        return None
    matchers = []
    for g in (s.strip().strip("`") for s in scope):
        if not g:
            continue
        if g.endswith("/"):
            matchers.append(("prefix", g))
        elif "*" in g:
            matchers.append(("regex", re.compile("^" + re.escape(g).replace(r"\*\*", ".*").replace(r"\*", "[^/]*") + "$")))
        else:
            matchers.append(("exact", g))
    out = []
    for f in changed:
        if f == "PROGRESS.md" or f.startswith("plan_docs/"):
            continue  # workflow infra, not phase code; user may fix a phase file mid-pause
        if not any((k == "prefix" and f.startswith(m)) or (k == "exact" and (f == m or f.startswith(m.rstrip("/") + "/")))
                   or (k == "regex" and m.match(f)) for k, m in matchers):
            out.append(f)
    return out


def code_fingerprint(base, changed):
    h = hashlib.sha256()
    h.update(git("rev-parse", "HEAD").strip().encode())
    h.update(b"\0" + base.encode())
    for diff in (git("diff", base, "HEAD"), git("diff"), git("diff", "--cached")):
        h.update(b"\0" + diff.encode("utf-8", "replace"))
    for f in changed:
        p = pathlib.Path(f)
        try:
            if p.is_file() and p.stat().st_size <= SCAN_MAX_BYTES:
                h.update(b"\0" + p.read_bytes())
        except Exception:
            pass
    return h.hexdigest()


def run_verify(verify_cmd, base, changed):
    fp_path, res_path = STATE / "verify_fingerprint", STATE / "verify_passed"
    # Include the command itself so a changed `## Verify` invalidates the cache even with identical code.
    fp = hashlib.sha256((code_fingerprint(base, changed) + "\0" + verify_cmd).encode()).hexdigest()
    if fp_path.exists() and res_path.exists() and _read(fp_path).strip() == fp:
        cached = _read(res_path).strip()
        if cached in ("true", "false"):
            return cached == "true", True, "reused cached result (no code change since last gate)"
    proc = subprocess.run(verify_cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    passed = proc.returncode == 0
    fp_path.write_text(fp)
    res_path.write_text("true" if passed else "false")
    (STATE / "verify_output.txt").write_text(
        (proc.stdout or "")[-4000:] + "\n--- stderr ---\n" + (proc.stderr or "")[-2000:], encoding="utf-8", errors="replace")
    return passed, False, "ran: " + verify_cmd


def main():
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    if not (STATE / "current_phase").exists():
        sys.exit(0)  # no active phase run (orchestrator writes current_phase) - stay out of ordinary sessions
    if not pathlib.Path(".git").exists():
        (STATE / "gate_result.json").write_text(json.dumps({"stop_recommended": True, "reasons": [
            "not a git repository (run `git init` + a baseline commit; the gate needs git for diffs/scope/rollback)"]}, indent=2))
        sys.stderr.write("GATE: not a git repository - `git init` required.\n")
        sys.exit(0)

    base = base_ref()
    phase_file = current_phase_file()
    verify_cmd, verify_src = resolve_verify(phase_file)
    changed = changed_files(base)
    n_files, n_lines = len(changed), changed_lines(base)
    added = added_content(base)
    destructive, pins, large = evaluate_destructive(added, changed), evaluate_pins(changed), evaluate_large(changed)
    scope = declared_scope(phase_file)
    out_of_scope = evaluate_scope(scope, changed)

    if verify_cmd == "skip":
        tests_passed, verify_cached, verify_note = True, False, "verify intentionally skipped (## Verify: skip)"
    elif verify_cmd is None:
        tests_passed, verify_cached, verify_note = None, False, "no verify command found"
    else:
        tests_passed, verify_cached, verify_note = run_verify(verify_cmd, base, changed)

    reasons = []
    if tests_passed is None:
        reasons.append("no verify command (add a `## Verify` section, set PHASERUN_VERIFY_CMD, or write `## Verify` -> skip)")
    elif tests_passed is False:
        reasons.append("verify failed: " + (verify_cmd or ""))
    if n_files > MAX_FILES:
        reasons.append("diff too large: %d files (> %d)" % (n_files, MAX_FILES))
    if n_lines > MAX_LINES:
        reasons.append("diff too large: %d lines (> %d)" % (n_lines, MAX_LINES))
    if destructive:
        reasons.append("destructive op(s) in added code: " + ", ".join(destructive))
    if pins:
        reasons.append("dependency pins changed (may shift results): " + ", ".join(pins))
    if large:
        reasons.append("large artifact(s) - gitignore or use DVC/LFS: " + ", ".join(large))
    if scope is None:
        reasons.append("could not parse phase Scope - cannot verify boundaries")
    elif out_of_scope:
        reasons.append("out-of-scope files: " + ", ".join(out_of_scope[:5]) + (" ..." if len(out_of_scope) > 5 else ""))

    (STATE / "gate_result.json").write_text(json.dumps({
        "phase_file": str(phase_file) if phase_file else None, "base_ref": base, "verify_cmd": verify_cmd,
        "verify_source": verify_src, "verify_cached": verify_cached, "verify_note": verify_note,
        "tests_passed": tests_passed, "files_touched": n_files, "lines_changed": n_lines,
        "destructive_ops": destructive, "dependency_pins_changed": pins, "large_artifacts": large,
        "out_of_scope_files": out_of_scope or [], "scope_parsed": scope is not None,
        "stop_recommended": len(reasons) > 0, "reasons": reasons}, indent=2))
    sys.stderr.write(("GATE: stop recommended - " + "; ".join(reasons) + "\n") if reasons
                     else ("GATE: clean - safe to advance (" + verify_note + ").\n"))
    sys.exit(0)


if __name__ == "__main__":
    main()
