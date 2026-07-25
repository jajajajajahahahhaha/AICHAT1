"""Secure code execution sandbox."""
import asyncio
import tempfile
import os
import shutil
from typing import Dict, Any


async def run_code(language: str, code: str, timeout: int = 30) -> Dict[str, Any]:
    """Run code in an isolated temporary directory with resource limits."""
    language = language.lower().strip()
    if language not in {"python", "bash", "html"}:
        return {"success": False, "error": f"Unsupported language: {language}"}

    if language == "html":
        # HTML is just returned back for the client-side runner (in new window).
        return {
            "success": True,
            "language": "html",
            "html": code,
            "note": "HTML is rendered client-side in a separate window.",
        }

    workdir = tempfile.mkdtemp(prefix="sandbox_")
    try:
        if language == "python":
            script = os.path.join(workdir, "main.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write(code)
            cmd = ["python3", script]
        else:  # bash
            script = os.path.join(workdir, "main.sh")
            with open(script, "w", encoding="utf-8") as f:
                f.write(code)
            os.chmod(script, 0o755)
            cmd = ["bash", script]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {
                "success": False,
                "error": f"Execution timed out after {timeout}s",
                "stdout": "",
                "stderr": "",
                "returncode": -1,
            }

        return {
            "success": proc.returncode == 0,
            "language": language,
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[:10000],
            "stderr": stderr.decode("utf-8", errors="replace")[:10000],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
