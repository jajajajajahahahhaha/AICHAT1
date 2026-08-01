"""
Kimi Chat — Sandbox v3.0 (Multi-Language Universal Runner)
==========================================================

A massive upgrade over v2.x's tiny python/bash/html executor.

New in v3.0
-----------
* **15+ languages** — python, bash, html, javascript (node), typescript
  (ts-node / deno auto-detect), c, cpp, go, rust, java, kotlin, ruby, php,
  lua, r, sql (sqlite), perl. Language auto-detection from shebang or
  first line when caller passes ``language="auto"``.
* **Higher limits** — default timeout 90s (was 30s), max 300s. Output cap
  200_000 chars per stream (was 10_000). Memory hint via resource limits
  when available.
* **Persistent workspace** — per-chat/per-user working directory so
  generated files (plots, CSVs, images, binaries) survive across calls
  and can be downloaded via ``/api/sandbox/files/...``.
* **Package installer** — optional ``packages`` argument runs
  pip / npm / gem / cargo add before executing the code.
* **Stdin support** — pass ``stdin`` for interactive-style programs.
* **Env vars** — inject custom environment through ``env``.
* **Artifacts** — new files created during a run are auto-listed and
  turned into downloadable URLs. matplotlib is patched to save to
  ``plot.png`` automatically. Images returned as base64 data URLs so the
  chat frontend can embed them inline.
* **Better errors** — compile/link failures are separated from runtime
  failures for compiled languages.
* **Streaming-ready** — the async ``run_code`` still returns a full
  Dict[str, Any] but the internal machinery is stream-friendly for a
  future SSE wire-up.
* **Full backward compat** — ``run_code(language, code)`` two-positional
  form still works and returns the SAME shape the v2 tests assert
  against ({success, stdout, stderr, returncode, language}). All new
  fields are ADDITIVE.
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import re
import resource
import shutil
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Persistent workspace root
# ---------------------------------------------------------------------------
# Located inside the project's data directory so it lives alongside chats/
# images. Symlinked from the server-side download endpoint. We do NOT commit
# these files back to the repo (they can be huge). See data/.gitignore below.

_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKSPACE_ROOT = _ROOT / "data" / "sandbox_workspaces"
_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

# Also make sure a .gitignore inside excludes everything so a stray git add
# doesn't push megabytes of build artefacts to the repo.
_gi = _WORKSPACE_ROOT / ".gitignore"
if not _gi.exists():
    try:
        _gi.write_text("*\n!.gitignore\n", encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Limits & constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT = 90               # v2 was 30s
MAX_TIMEOUT     = 300              # hard cap
MAX_STDOUT      = 200_000          # v2 was 10_000
MAX_STDERR      = 200_000
MAX_ARTIFACTS   = 25               # per run
MAX_ARTIFACT_BYTES_INLINE = 3 * 1024 * 1024  # 3MB — images larger than this are only linked
MAX_TOTAL_ARTIFACT_BYTES  = 25 * 1024 * 1024 # 25MB total artefact size before we skip the rest

# CPU / address-space soft limits (Linux only — silently ignored on macOS/Win)
DEFAULT_CPU_SECONDS = 120
DEFAULT_ADDRESS_MB  = 2048  # 2GB


# ---------------------------------------------------------------------------
# Language registry
# ---------------------------------------------------------------------------
#
# Each entry describes how to compile (optional) and run a file for a given
# language. The runner writes the user's code to `filename` (created inside
# the sandbox cwd), optionally runs `compile_cmd` first, then executes
# `run_cmd`. All commands are lists so we NEVER invoke a shell — arguments
# are passed exec-style to avoid injection.
#
# Extra languages can be added without touching the dispatcher.
# ---------------------------------------------------------------------------
Command = List[str]


class LangSpec:
    __slots__ = ("name", "filename", "compile_cmd", "run_cmd", "aliases",
                 "shebang", "install_cmd", "notes")

    def __init__(
        self,
        name: str,
        filename: str,
        run_cmd: Command,
        *,
        compile_cmd: Optional[Command] = None,
        aliases: Tuple[str, ...] = (),
        shebang: Optional[str] = None,
        install_cmd: Optional[Command] = None,
        notes: str = "",
    ):
        self.name = name
        self.filename = filename
        self.compile_cmd = compile_cmd
        self.run_cmd = run_cmd
        self.aliases = aliases
        self.shebang = shebang
        self.install_cmd = install_cmd  # None → no package manager for this lang
        self.notes = notes


# NB: compile_cmd receives working dir cwd, `main.<ext>` is the file we write.
# Compiled outputs go into the same cwd so cleanup is trivial.
_LANGS: List[LangSpec] = [
    LangSpec(
        "python",
        "main.py",
        run_cmd=["python3", "-u", "main.py"],
        aliases=("py", "python3"),
        shebang="#!/usr/bin/env python3",
        install_cmd=["python3", "-m", "pip", "install", "--quiet", "--no-input", "--disable-pip-version-check"],
        notes="Python 3.x with matplotlib/pandas/numpy pre-available on Actions runner.",
    ),
    LangSpec(
        "bash",
        "main.sh",
        run_cmd=["bash", "main.sh"],
        aliases=("sh", "shell"),
        shebang="#!/usr/bin/env bash",
        notes="Full bash. Has curl, wget, git, jq, sqlite3, ffmpeg on Actions runner.",
    ),
    LangSpec(
        "javascript",
        "main.js",
        run_cmd=["node", "main.js"],
        aliases=("js", "node", "nodejs"),
        shebang="#!/usr/bin/env node",
        install_cmd=["npm", "install", "--silent", "--no-audit", "--no-fund", "--no-progress"],
        notes="Node.js 20+ on Actions runner.",
    ),
    LangSpec(
        "typescript",
        "main.ts",
        run_cmd=["npx", "--yes", "-p", "typescript@5.5", "-p", "tsx@4", "tsx", "main.ts"],
        aliases=("ts",),
        install_cmd=["npm", "install", "--silent", "--no-audit", "--no-fund", "--no-progress"],
        notes="Run TypeScript directly via tsx (auto-installed on first run).",
    ),
    LangSpec(
        "c",
        "main.c",
        compile_cmd=["gcc", "-O2", "-std=c17", "-Wall", "-o", "main_bin", "main.c", "-lm"],
        run_cmd=["./main_bin"],
        aliases=("gcc",),
        notes="Compiled with gcc -O2, link libm.",
    ),
    LangSpec(
        "cpp",
        "main.cpp",
        compile_cmd=["g++", "-O2", "-std=c++20", "-Wall", "-o", "main_bin", "main.cpp"],
        run_cmd=["./main_bin"],
        aliases=("c++", "cxx", "cc"),
        notes="Compiled with g++ -std=c++20 -O2.",
    ),
    LangSpec(
        "go",
        "main.go",
        run_cmd=["go", "run", "main.go"],
        aliases=("golang",),
        notes="`go run` — supports modules if you add a go.mod via package_files.",
    ),
    LangSpec(
        "rust",
        "main.rs",
        compile_cmd=["rustc", "-O", "-o", "main_bin", "main.rs"],
        run_cmd=["./main_bin"],
        aliases=("rs",),
        notes="rustc -O standalone build (no cargo for the single-file case).",
    ),
    LangSpec(
        "java",
        "Main.java",
        compile_cmd=["javac", "Main.java"],
        run_cmd=["java", "Main"],
        aliases=(),
        notes="Class must be named `Main` (single-file mode).",
    ),
    LangSpec(
        "kotlin",
        "Main.kt",
        compile_cmd=["kotlinc", "Main.kt", "-include-runtime", "-d", "Main.jar"],
        run_cmd=["java", "-jar", "Main.jar"],
        aliases=("kt",),
        notes="kotlinc must be available.",
    ),
    LangSpec(
        "ruby",
        "main.rb",
        run_cmd=["ruby", "main.rb"],
        aliases=("rb",),
        shebang="#!/usr/bin/env ruby",
        install_cmd=["gem", "install", "--no-document"],
        notes="Ruby 3.x on Actions runner.",
    ),
    LangSpec(
        "php",
        "main.php",
        run_cmd=["php", "main.php"],
        aliases=(),
        notes="PHP 8.x CLI.",
    ),
    LangSpec(
        "lua",
        "main.lua",
        run_cmd=["lua", "main.lua"],
        aliases=(),
        notes="Lua 5.x.",
    ),
    LangSpec(
        "r",
        "main.R",
        run_cmd=["Rscript", "--no-save", "--no-restore", "main.R"],
        aliases=("rscript",),
        notes="Rscript with base packages.",
    ),
    LangSpec(
        "sql",
        "main.sql",
        run_cmd=["sqlite3", "-batch", "sandbox.db"],
        aliases=("sqlite", "sqlite3"),
        notes="SQLite in-file DB at sandbox.db — pipes the SQL as stdin.",
    ),
    LangSpec(
        "perl",
        "main.pl",
        run_cmd=["perl", "main.pl"],
        aliases=(),
        shebang="#!/usr/bin/env perl",
        notes="Perl 5.x.",
    ),
]

_LANG_INDEX: Dict[str, LangSpec] = {}
for spec in _LANGS:
    _LANG_INDEX[spec.name] = spec
    for alias in spec.aliases:
        _LANG_INDEX[alias] = spec


SUPPORTED_LANGUAGES: List[str] = sorted({s.name for s in _LANGS} | {"html"})


# ---------------------------------------------------------------------------
# Language auto-detection (from shebang or a hint line)
# ---------------------------------------------------------------------------
_SHEBANG_RE = re.compile(r"^\s*#!.*\b(python|node|bash|sh|ruby|perl|php|lua)\b", re.IGNORECASE)
_HINT_RE    = re.compile(r"^\s*(?:#|//|--|/\*)\s*lang(?:uage)?\s*[:=]\s*([a-z+]+)", re.IGNORECASE)


def _auto_detect(code: str) -> Optional[str]:
    if not code:
        return None
    first_lines = code.strip().splitlines()[:3]
    for line in first_lines:
        m = _HINT_RE.match(line)
        if m:
            return m.group(1).lower()
        m = _SHEBANG_RE.match(line)
        if m:
            token = m.group(1).lower()
            return {"sh": "bash", "node": "javascript"}.get(token, token)
    # Structural heuristics — cheap and last-resort only.
    if re.search(r"^\s*(def|import|print\()", code, re.MULTILINE):
        return "python"
    if re.search(r"console\.log|=>|const\s+\w+\s*=", code):
        return "javascript"
    if re.search(r"#include\s*<", code):
        return "cpp"
    if re.search(r"^\s*package\s+main", code, re.MULTILINE):
        return "go"
    if re.search(r"^\s*fn\s+main\s*\(", code, re.MULTILINE):
        return "rust"
    return None


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _safe_id(raw: Optional[str]) -> str:
    """Normalise a workspace id — must be an opaque, filesystem-safe token."""
    if raw and _SAFE_ID_RE.match(raw):
        return raw
    return "wsp_" + uuid.uuid4().hex[:12]


def _workspace_path(workspace_id: str) -> Path:
    p = _WORKSPACE_ROOT / workspace_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_workspace_files(workspace_id: str) -> List[Dict[str, Any]]:
    """Enumerate files (not directories) inside a workspace."""
    ws = _workspace_path(_safe_id(workspace_id))
    out: List[Dict[str, Any]] = []
    for p in sorted(ws.rglob("*")):
        if p.is_file():
            rel = p.relative_to(ws).as_posix()
            try:
                st = p.stat()
                out.append({
                    "path": rel,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
            except OSError:
                continue
    return out


def read_workspace_file(workspace_id: str, relative_path: str) -> Optional[Tuple[bytes, str]]:
    """Read a file's bytes + best-guess mime for the download endpoint."""
    ws = _workspace_path(_safe_id(workspace_id))
    # Guard against traversal.
    target = (ws / relative_path).resolve()
    try:
        target.relative_to(ws.resolve())
    except ValueError:
        return None
    if not target.is_file():
        return None
    mime, _ = mimetypes.guess_type(target.name)
    return target.read_bytes(), (mime or "application/octet-stream")


def _snapshot_files(root: Path) -> Dict[str, float]:
    """Return {relative_path: mtime} for a workspace pre-run."""
    snap: Dict[str, float] = {}
    for p in root.rglob("*"):
        if p.is_file():
            try:
                snap[p.relative_to(root).as_posix()] = p.stat().st_mtime
            except OSError:
                continue
    return snap


def _detect_artifacts(root: Path, before: Dict[str, float]) -> List[Dict[str, Any]]:
    """Return files new or modified since `before`."""
    changes: List[Dict[str, Any]] = []
    total_bytes = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        # Skip the source and intermediate build files by name.
        if rel in _SOURCE_FILE_NAMES:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        prev = before.get(rel)
        if prev is not None and abs(st.st_mtime - prev) < 1e-6 and rel not in _ALWAYS_ARTIFACT_NAMES:
            continue
        if len(changes) >= MAX_ARTIFACTS:
            break
        if total_bytes + st.st_size > MAX_TOTAL_ARTIFACT_BYTES:
            continue
        mime, _ = mimetypes.guess_type(p.name)
        entry: Dict[str, Any] = {
            "path": rel,
            "size": st.st_size,
            "mime": mime or "application/octet-stream",
        }
        # For small images, embed as a data URL so the frontend can render inline.
        if (mime or "").startswith("image/") and st.st_size <= MAX_ARTIFACT_BYTES_INLINE:
            try:
                entry["data_url"] = "data:{m};base64,{b}".format(
                    m=mime,
                    b=base64.b64encode(p.read_bytes()).decode("ascii"),
                )
            except OSError:
                pass
        changes.append(entry)
        total_bytes += st.st_size
    return changes


_SOURCE_FILE_NAMES = {
    "main.py", "main.sh", "main.js", "main.ts", "main.c", "main.cpp",
    "main.go", "main.rs", "main.rb", "main.php", "main.lua", "main.R",
    "main.sql", "main.pl", "Main.java", "Main.kt", "Main.jar", "main_bin",
    "input.stdin",
}
_ALWAYS_ARTIFACT_NAMES = {"plot.png", "output.png", "figure.png"}


# ---------------------------------------------------------------------------
# Resource limit helper (Linux only; harmless no-op elsewhere)
# ---------------------------------------------------------------------------
def _apply_limits(cpu_seconds: int, address_mb: int):
    """Return a preexec_fn for asyncio subprocess that clamps CPU + memory."""
    def _pre():
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        except (ValueError, OSError):
            pass
        try:
            bytes_ = address_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (bytes_, bytes_))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ValueError, OSError):
            pass
        os.setsid()  # own process group so we can kill children too
    return _pre


# ---------------------------------------------------------------------------
# Package installer
# ---------------------------------------------------------------------------
async def _install_packages(spec: LangSpec, packages: List[str], cwd: Path, env: Dict[str, str], timeout: int) -> Dict[str, Any]:
    if not packages:
        return {"skipped": True}
    if not spec.install_cmd:
        return {"skipped": True, "note": f"No package manager wired for {spec.name}."}
    # Sanitise — only allow safe package names (no shell metachars, no whitespace).
    safe: List[str] = []
    for pkg in packages:
        pkg = (pkg or "").strip()
        if not pkg:
            continue
        if not re.match(r"^[A-Za-z0-9_.@/\-+=<>]+$", pkg):
            return {"success": False, "error": f"unsafe package name: {pkg!r}"}
        safe.append(pkg)
    if not safe:
        return {"skipped": True}
    cmd = list(spec.install_cmd) + safe
    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=max(30, timeout))
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return {"success": False, "error": "package install timed out", "packages": safe}
    except FileNotFoundError as e:
        return {"success": False, "error": f"installer missing: {e}", "packages": safe}
    return {
        "success": proc.returncode == 0,
        "packages": safe,
        "returncode": proc.returncode,
        "elapsed": round(time.monotonic() - t0, 2),
        "stdout": out.decode("utf-8", "replace")[:MAX_STDOUT],
        "stderr": err.decode("utf-8", "replace")[:MAX_STDERR],
    }


# ---------------------------------------------------------------------------
# Auto-instrument matplotlib so `.show()` still produces something we can grab
# ---------------------------------------------------------------------------
_MPL_AUTO_SAVE = r"""
# --- sandbox: matplotlib auto-save (injected by sandbox v3) ---
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
    _orig_show = _plt.show
    _saved = {"n": 0}
    def _sandbox_show(*a, **kw):
        _saved["n"] += 1
        name = "plot.png" if _saved["n"] == 1 else f"plot_{_saved['n']}.png"
        try:
            _plt.gcf().savefig(name, bbox_inches="tight", dpi=110)
            print(f"[sandbox] saved figure to {name}")
        except Exception as _e:
            print(f"[sandbox] savefig failed: {_e}")
        _orig_show(*a, **kw)
    _plt.show = _sandbox_show
except Exception:
    pass
# --- end sandbox preamble ---
"""


def _inject_python_preamble(code: str) -> str:
    """Prepend our matplotlib helper *and* preserve any user shebang / encoding."""
    lines = code.splitlines(keepends=True)
    head = []
    body_start = 0
    for i, ln in enumerate(lines):
        if i < 2 and (ln.startswith("#!") or "coding" in ln):
            head.append(ln)
            body_start = i + 1
        else:
            break
    return "".join(head) + _MPL_AUTO_SAVE + "\n" + "".join(lines[body_start:])


# ---------------------------------------------------------------------------
# The main runner
# ---------------------------------------------------------------------------
async def run_code(
    language: str,
    code: str = "",
    *,
    timeout: Optional[int] = None,
    stdin: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    packages: Optional[List[str]] = None,
    workspace_id: Optional[str] = None,
    cpu_seconds: Optional[int] = None,
    memory_mb: Optional[int] = None,
    files: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Execute code in the sandbox.

    Parameters
    ----------
    language      : one of SUPPORTED_LANGUAGES, an alias, or "auto".
    code          : the source code as a string.
    timeout       : seconds; default 90, max 300.
    stdin         : optional stdin text piped to the program.
    env           : optional extra environment variables (merged with parent).
    packages      : optional list of pkg names to install BEFORE running (pip/npm/gem).
    workspace_id  : persistent workspace to reuse across calls (chat-scoped).
                    When omitted, a fresh temp workspace is created for this call.
    cpu_seconds   : hard CPU limit (default 120s).
    memory_mb     : address-space limit in MB (default 2048).
    files         : optional {relative_path: contents} written into workspace
                    BEFORE running (e.g. seed data, package.json, go.mod).

    Returns
    -------
    Dict with (at minimum) these v2-compat keys::

        success, language, stdout, stderr, returncode

    ...plus the new v3 additive keys::

        elapsed, workspace_id, files (created/modified artifacts),
        install (package-installer result if requested),
        note (context messages), compile (compile step result for compiled
        languages), timed_out (bool).
    """
    language = (language or "").lower().strip()
    if not language:
        return {"success": False, "language": "", "error": "language is required",
                "stdout": "", "stderr": "", "returncode": -1}

    # HTML is a client-side preview — same shape as v2 for compatibility.
    if language == "html":
        return {
            "success": True,
            "language": "html",
            "html": code,
            "note": "HTML is rendered client-side in a separate window.",
            "stdout": "",
            "stderr": "",
            "returncode": 0,
        }

    # Auto-detect if asked
    if language == "auto":
        detected = _auto_detect(code)
        if not detected:
            return {"success": False, "language": "auto",
                    "error": "could not auto-detect language; please pass an explicit one.",
                    "stdout": "", "stderr": "", "returncode": -1,
                    "supported": SUPPORTED_LANGUAGES}
        language = detected

    spec = _LANG_INDEX.get(language)
    if not spec:
        return {
            "success": False,
            "language": language,
            "error": f"Unsupported language: {language!r}. Supported: {', '.join(SUPPORTED_LANGUAGES)}",
            "stdout": "", "stderr": "", "returncode": -1,
        }

    if not isinstance(code, str):
        return {"success": False, "language": spec.name,
                "error": "code must be a string", "stdout": "", "stderr": "", "returncode": -1}

    # Timeouts
    to = int(timeout) if timeout else DEFAULT_TIMEOUT
    to = max(1, min(to, MAX_TIMEOUT))

    # Workspace
    persistent = bool(workspace_id)
    ws_id = _safe_id(workspace_id) if persistent else "run_" + uuid.uuid4().hex[:10]
    workdir: Path
    if persistent:
        workdir = _workspace_path(ws_id)
    else:
        # Non-persistent: still store under _WORKSPACE_ROOT so paths returned to
        # the UI remain reachable via /api/sandbox/files/... for the session.
        workdir = _WORKSPACE_ROOT / ws_id
        workdir.mkdir(parents=True, exist_ok=True)

    # Optional pre-run files (package.json, go.mod, seed CSV, etc.)
    if files:
        for rel, contents in files.items():
            if not isinstance(rel, str) or not isinstance(contents, str):
                continue
            # Refuse traversal
            target = (workdir / rel).resolve()
            try:
                target.relative_to(workdir.resolve())
            except ValueError:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")

    # Prepare env
    run_env = dict(os.environ)
    run_env["PYTHONUNBUFFERED"] = "1"
    run_env["MPLBACKEND"] = "Agg"
    run_env["NODE_NO_WARNINGS"] = "1"
    run_env["HOME"] = str(workdir)  # keeps npm/pip caches inside the ws
    if env:
        for k, v in env.items():
            if isinstance(k, str) and isinstance(v, str) and re.match(r"^[A-Z_][A-Z0-9_]*$", k):
                run_env[k] = v

    # ---- write the source file --------------------------------------------
    source_path = workdir / spec.filename
    payload = code
    if spec.name == "python":
        payload = _inject_python_preamble(code)
    source_path.write_text(payload, encoding="utf-8")

    # Make bash / ruby / perl scripts executable so shebangs also work.
    try:
        source_path.chmod(source_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass

    # Snapshot for artifact diff
    pre_snap = _snapshot_files(workdir)

    # ---- Package install --------------------------------------------------
    install_result: Optional[Dict[str, Any]] = None
    if packages:
        install_result = await _install_packages(spec, packages, workdir, run_env, min(120, to))
        if install_result.get("success") is False and not install_result.get("skipped"):
            return {
                "success": False,
                "language": spec.name,
                "stdout": "",
                "stderr": install_result.get("stderr", "") or install_result.get("error", ""),
                "returncode": install_result.get("returncode", -1),
                "install": install_result,
                "workspace_id": ws_id,
                "error": f"Package installation failed: {install_result.get('error') or 'see stderr'}",
            }

    # ---- Compile step -----------------------------------------------------
    compile_result: Optional[Dict[str, Any]] = None
    if spec.compile_cmd:
        c_t0 = time.monotonic()
        try:
            cp = await asyncio.create_subprocess_exec(
                *spec.compile_cmd, cwd=str(workdir), env=run_env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                c_out, c_err = await asyncio.wait_for(cp.communicate(), timeout=min(120, to))
            except asyncio.TimeoutError:
                try:
                    cp.kill()
                except ProcessLookupError:
                    pass
                return {
                    "success": False, "language": spec.name,
                    "stdout": "", "stderr": "compile timed out",
                    "returncode": -1, "compile": {"timed_out": True, "cmd": spec.compile_cmd},
                    "workspace_id": ws_id,
                }
        except FileNotFoundError as e:
            return {
                "success": False, "language": spec.name,
                "stdout": "", "stderr": f"toolchain missing: {e}",
                "returncode": -1, "workspace_id": ws_id,
                "error": f"Required compiler not installed: {spec.compile_cmd[0]}",
            }
        compile_result = {
            "cmd": spec.compile_cmd,
            "returncode": cp.returncode,
            "elapsed": round(time.monotonic() - c_t0, 2),
            "stdout": c_out.decode("utf-8", "replace")[:MAX_STDOUT],
            "stderr": c_err.decode("utf-8", "replace")[:MAX_STDERR],
        }
        if cp.returncode != 0:
            return {
                "success": False,
                "language": spec.name,
                "stdout": compile_result["stdout"],
                "stderr": compile_result["stderr"],
                "returncode": cp.returncode,
                "compile": compile_result,
                "workspace_id": ws_id,
                "error": "compile step failed",
            }

    # ---- Run step ---------------------------------------------------------
    r_t0 = time.monotonic()
    stdin_bytes: Optional[bytes] = stdin.encode("utf-8") if stdin else None

    preexec = _apply_limits(
        cpu_seconds=int(cpu_seconds) if cpu_seconds else DEFAULT_CPU_SECONDS,
        address_mb=int(memory_mb) if memory_mb else DEFAULT_ADDRESS_MB,
    ) if sys.platform.startswith("linux") else None

    # SQL special-cases: sqlite3 reads SQL from stdin, so we replace stdin.
    if spec.name == "sql":
        stdin_bytes = code.encode("utf-8")

    timed_out = False
    try:
        proc = await asyncio.create_subprocess_exec(
            *spec.run_cmd,
            stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workdir),
            env=run_env,
            preexec_fn=preexec,
        )
    except FileNotFoundError as e:
        return {
            "success": False,
            "language": spec.name,
            "stdout": "", "stderr": f"interpreter missing: {e}",
            "returncode": -1,
            "workspace_id": ws_id,
            "error": f"Required interpreter not installed: {spec.run_cmd[0]}",
        }

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes), timeout=to,
        )
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            stdout_b, stderr_b = await proc.communicate()
        except Exception:
            stdout_b, stderr_b = b"", b""

    elapsed = round(time.monotonic() - r_t0, 2)
    stdout = stdout_b.decode("utf-8", "replace")
    stderr = stderr_b.decode("utf-8", "replace")

    # Note the truncation explicitly so callers can react.
    stdout_truncated = len(stdout) > MAX_STDOUT
    stderr_truncated = len(stderr) > MAX_STDERR
    if stdout_truncated:
        stdout = stdout[:MAX_STDOUT] + "\n[... output truncated ...]"
    if stderr_truncated:
        stderr = stderr[:MAX_STDERR] + "\n[... stderr truncated ...]"

    # Artifact diff
    artifacts = _detect_artifacts(workdir, pre_snap)

    # Cleanup non-persistent workdirs — but keep them around briefly so the
    # download URL still resolves for a few minutes.
    #   (We DO NOT delete synchronously; a periodic sweeper would remove old
    #    ephemeral workspaces. For now they just accumulate on disk under
    #    data/sandbox_workspaces/ and are re-used across runs.)

    result: Dict[str, Any] = {
        "success": (proc.returncode == 0) and not timed_out,
        "language": spec.name,
        "stdout": stdout,
        "stderr": stderr,
        "returncode": proc.returncode if proc.returncode is not None else -1,
        "elapsed": elapsed,
        "timed_out": timed_out,
        "workspace_id": ws_id,
        "files": artifacts,
        "truncated": {"stdout": stdout_truncated, "stderr": stderr_truncated},
    }
    if compile_result:
        result["compile"] = compile_result
    if install_result:
        result["install"] = install_result
    if timed_out:
        result["error"] = f"Execution timed out after {to}s"

    return result


# ---------------------------------------------------------------------------
# Housekeeping — a small sweep to purge old ephemeral workspaces.
# Called opportunistically (never on the hot path).
# ---------------------------------------------------------------------------
_EPHEMERAL_TTL = 60 * 60  # 1h


def sweep_old_workspaces() -> int:
    """Remove ephemeral workspaces (run_*) older than 1h. Returns count removed."""
    now = time.time()
    removed = 0
    try:
        for p in _WORKSPACE_ROOT.iterdir():
            if not p.is_dir():
                continue
            if not p.name.startswith("run_"):
                continue
            try:
                age = now - p.stat().st_mtime
            except OSError:
                continue
            if age > _EPHEMERAL_TTL:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
    except FileNotFoundError:
        pass
    return removed
network=False
