"""Shared plumbing for scenario checks: artifact IO, port-forward HTTP probes,
stdout-log parsing, and conflict-marker scanning. Moved verbatim from the former
harness/evaluate.py god-module; no behavior change."""

import json
import os
import socket
import subprocess
import time
import urllib.request

from harness import tools
from harness.paths import KUBECONFIG, VERSIONS


def _load(run_dir, name):
    try:
        return json.loads((run_dir / name).read_text())
    except (json.JSONDecodeError, ValueError, FileNotFoundError):
        return {}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_health(namespace: str, release: str) -> tuple[bool, str]:
    port = _free_port()
    exe = tools.which("kubectl") or "kubectl"
    env = dict(os.environ)
    env["KUBECONFIG"] = str(KUBECONFIG)
    env["PATH"] = tools.PATH
    proc = subprocess.Popen(
        [exe, "port-forward", f"svc/{release}", f"{port}:{VERSIONS.get('SVC_PORT', '80')}",
         "-n", namespace],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        deadline = time.time() + 25
        last = "no attempt"
        while time.time() < deadline:
            if proc.poll() is not None:
                out = (proc.stdout.read() if proc.stdout else "").strip()
                return False, f"port-forward exited early: {out[:200]}"
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2
                ) as resp:
                    code = resp.status
                    body = resp.read().decode()
                if code == 200 and json.loads(body) == {"status": "ok"}:
                    return True, f'GET /health -> 200 {body.strip()}'
                last = f"got {code} {body.strip()[:80]}"
            except Exception as e:  # noqa: BLE001 - transient during forward setup
                last = f"{type(e).__name__}: {e}"
            time.sleep(1)
        return False, f'GET /health did not return 200 {{"status":"ok"}} within 25s ({last})'
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _http_get_json(namespace: str, release: str, path: str) -> tuple[bool, object, str]:
    """Port-forward to svc/<release> and GET <path>, parsing a JSON body.
    Returns (ok, parsed_or_None, detail). Mirrors _http_health's forward loop."""
    port = _free_port()
    exe = tools.which("kubectl") or "kubectl"
    env = dict(os.environ)
    env["KUBECONFIG"] = str(KUBECONFIG)
    env["PATH"] = tools.PATH
    proc = subprocess.Popen(
        [exe, "port-forward", f"svc/{release}", f"{port}:{VERSIONS.get('SVC_PORT', '80')}",
         "-n", namespace],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        deadline = time.time() + 25
        last = "no attempt"
        while time.time() < deadline:
            if proc.poll() is not None:
                out = (proc.stdout.read() if proc.stdout else "").strip()
                return False, None, f"port-forward exited early: {out[:200]}"
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{path}", timeout=2
                ) as resp:
                    code = resp.status
                    body = resp.read().decode()
                if code == 200:
                    try:
                        return True, json.loads(body), f"GET {path} -> 200 {body.strip()[:120]}"
                    except ValueError:
                        return False, None, f"GET {path} -> 200 non-JSON body {body.strip()[:120]}"
                last = f"got {code} {body.strip()[:80]}"
            except Exception as e:  # noqa: BLE001 - transient during forward setup
                last = f"{type(e).__name__}: {e}"
            time.sleep(1)
        return False, None, f"GET {path} did not return 200 within 25s ({last})"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _burst_health(namespace: str, release: str, n: int) -> int:
    """Open one port-forward to svc/<release>, wait until it serves, then issue
    exactly `n` GET /health requests. Returns how many returned HTTP 200. A
    fixed synthetic load so stdout line counts are comparable run to run."""
    port = _free_port()
    exe = tools.which("kubectl") or "kubectl"
    env = dict(os.environ)
    env["KUBECONFIG"] = str(KUBECONFIG)
    env["PATH"] = tools.PATH
    proc = subprocess.Popen(
        [exe, "port-forward", f"svc/{release}", f"{port}:{VERSIONS.get('SVC_PORT', '80')}",
         "-n", namespace],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if proc.poll() is not None:
                return 0
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read()
                break
            except Exception:  # noqa: BLE001 - transient during forward setup
                time.sleep(1)
        else:
            return 0
        ok = 0
        for _ in range(n):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                    if r.status == 200:
                        ok += 1
            except Exception:  # noqa: BLE001
                pass
        return ok
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def measure_stdout_lines(namespace: str, release: str, n: int = 10) -> int:
    """Issue a fixed synthetic load of `n` GET /health requests, let uvicorn
    flush its access logs, then count non-blank stdout lines from
    `kubectl logs deployment/<release>`. Identical procedure for a base run and a
    scenario run, so the counts compare directly. Raises on a kubectl failure."""
    _burst_health(namespace, release, n)
    time.sleep(2)
    r = tools.kubectl(["logs", f"deployment/{release}", "-n", namespace, "--tail=-1"], check=False)
    if r.returncode != 0:
        raise RuntimeError(f"kubectl logs failed: {(r.stderr or r.stdout or '').strip()[:200]}")
    return sum(1 for ln in (r.stdout or "").splitlines() if ln.strip())


def _log_lines(run_dir) -> list[str]:
    """Non-blank stdout lines from the run's collected pod logs
    (logs/*.log, excluding *.previous.log)."""
    out: list[str] = []
    logs_dir = run_dir / "logs"
    if logs_dir.is_dir():
        for lf in sorted(logs_dir.glob("*.log")):
            if lf.name.endswith(".previous.log"):
                continue
            for ln in lf.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.strip():
                    out.append(ln)
    return out


def _parse_json_logs(lines) -> tuple[list[dict], int]:
    """(list of JSON-object lines, total non-blank line count)."""
    objs: list[dict] = []
    for ln in lines:
        try:
            v = json.loads(ln)
        except ValueError:
            continue
        if isinstance(v, dict):
            objs.append(v)
    return objs, len(lines)


_CONFLICT_SKIP_PARTS = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache", "node_modules"}


def _conflict_marker_files(root):
    """Files under `root` (recursively) that carry a Git conflict-marker line
    (^<<<<<<< , ^=======$, ^>>>>>>> ). Binary/undecodable files are skipped."""
    hits = []
    if not root.is_dir():
        return hits
    for f in sorted(root.rglob("*")):
        if not f.is_file() or _CONFLICT_SKIP_PARTS.intersection(f.parts):
            continue
        try:
            txt = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for ln in txt.splitlines():
            if ln == "=======" or ln.startswith("<<<<<<< ") or ln.startswith(">>>>>>> "):
                hits.append(f.relative_to(root).as_posix())
                break
    return hits
