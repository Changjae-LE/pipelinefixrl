"""Step 7: deterministic checks against collected state. No LLM judging."""

import json
import os
import pathlib
import socket
import subprocess
import time
import urllib.request

from harness import tools
from harness.paths import KUBECONFIG, VERSIONS

# id -> weight. Weights of non-NA checks are the scoring denominator.
CHECK_WEIGHTS = {
    "helm_release_ok": 10,
    "rollout_complete": 20,
    "deployment_ready": 20,
    "pods_ready": 15,
    "endpoints_present": 15,
    "http_health_ok": 20,
    "no_bad_events": 0,  # report-only
}

_BAD_EVENT_REASONS = {
    "Unhealthy",
    "BackOff",
    "CrashLoopBackOff",
    "FailedScheduling",
    "FailedMount",
    "FailedCreatePodSandBox",
    "ErrImageNeverPull",
}

# --- scenario-specific check registry (append-only; base-v2 / M-BE) -----------
# A scenario milestone registers extra deterministic checks here WITHOUT editing
# evaluate() or the six core checks. A run activates them via
# meta["scenario_checks"] (list of ids); meta["weight_overrides"] (id -> int)
# reallocates weight from the backbone so every scenario still totals 100.
# When neither key is present (base app, scenario-001) evaluate() is unchanged.
_SCENARIO_CHECKS: dict = {}


def register_scenario_check(check_id: str, default_weight: int):
    def _deco(fn):
        _SCENARIO_CHECKS[check_id] = (default_weight, fn)
        return fn

    return _deco


def _load(run_dir: pathlib.Path, name: str):
    try:
        return json.loads((run_dir / name).read_text())
    except (json.JSONDecodeError, ValueError, FileNotFoundError):
        return {}


_IMAGE_PROBLEM_REASONS = {
    "ErrImageNeverPull",
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
}


@register_scenario_check("image_pull_ok", 15)
def _image_pull_ok(*, run_dir, namespace, release, meta):
    """PASS iff no container is stuck in an image-acquisition waiting state.
    Owning scenario: scenario-002 (produces ErrImageNeverPull)."""
    pods = _load(run_dir, "pods.json")
    items = pods.get("items", []) if isinstance(pods, dict) else []
    hits = []
    for p in items:
        st = p.get("status") or {}
        for cs in (st.get("containerStatuses") or []) + (st.get("initContainerStatuses") or []):
            reason = ((cs.get("state") or {}).get("waiting") or {}).get("reason")
            if reason in _IMAGE_PROBLEM_REASONS:
                hits.append(f"{cs.get('name')}: {reason}")
    if hits:
        return False, "image acquisition failed — " + "; ".join(hits)
    return True, "no image-acquisition waiting states"


@register_scenario_check("no_oomkill", 10)
def _no_oomkill(*, run_dir, namespace, release, meta):
    """PASS iff no container was OOM-killed and total restarts are within the
    threshold. Owning scenario: scenario-003 (16Mi memory limit -> exit 137)."""
    threshold = int(VERSIONS.get("RESTART_THRESHOLD", "0"))
    pods = _load(run_dir, "pods.json")
    items = pods.get("items", []) if isinstance(pods, dict) else []
    oom, restarts = [], 0
    for p in items:
        for cs in (p.get("status") or {}).get("containerStatuses") or []:
            restarts += cs.get("restartCount", 0)
            term = (cs.get("lastState") or {}).get("terminated") or {}
            if term.get("reason") == "OOMKilled":
                oom.append(f"{cs.get('name')}: exit {term.get('exitCode')}")
    if oom:
        return False, "OOMKilled — " + "; ".join(oom)
    if restarts > threshold:
        return False, f"restartCount total {restarts} > threshold {threshold}"
    return True, f"no OOMKill; restarts {restarts} <= {threshold}"


@register_scenario_check("service_selects_pods", 15)
def _service_selects_pods(*, run_dir, namespace, release, meta):
    """PASS iff the Service's selector is non-empty and structurally equal to the
    Deployment's selector.matchLabels, AND at least one EndpointSlice endpoint
    resolves to a Ready pod carrying those labels. Owning scenario: scenario-005
    (selector hardcoded to a non-matching label set)."""
    svcs = _load(run_dir, "services.json")
    svc = next(
        (s for s in (svcs.get("items") or []) if (s.get("metadata") or {}).get("name") == release),
        None,
    )
    if svc is None:
        return False, f"Service {release!r} not found"
    sel = (svc.get("spec") or {}).get("selector") or {}
    if not sel:
        return False, "Service .spec.selector is empty"

    dep = _load(run_dir, "rollout.json")
    want = ((dep.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}
    if sel != want:
        return False, f"Service selector {sel} != Deployment matchLabels {want}"

    pods = {
        (p.get("metadata") or {}).get("name"): p
        for p in (_load(run_dir, "pods.json").get("items") or [])
    }
    ready_targets = 0
    for sl in _load(run_dir, "endpointslices.json").get("items") or []:
        labels = (sl.get("metadata") or {}).get("labels") or {}
        if labels.get("kubernetes.io/service-name") != release:
            continue
        for ep in sl.get("endpoints") or []:
            if not (ep.get("conditions") or {}).get("ready"):
                continue
            tref = ep.get("targetRef") or {}
            if tref.get("kind") != "Pod":
                continue
            pod = pods.get(tref.get("name"))
            if not pod:
                continue
            plabels = (pod.get("metadata") or {}).get("labels") or {}
            pconds = {c["type"]: c["status"] for c in (pod.get("status") or {}).get("conditions") or []}
            if pconds.get("Ready") == "True" and all(plabels.get(k) == v for k, v in want.items()):
                ready_targets += 1
    if ready_targets < 1:
        return False, "no EndpointSlice endpoint resolves to a Ready pod of the Deployment"
    return True, f"selector matches Deployment; {ready_targets} ready endpoint(s)"


def _applied_container_sc(run_dir):
    """The first app container's applied securityContext from pods.json."""
    pods = _load(run_dir, "pods.json")
    items = pods.get("items", []) if isinstance(pods, dict) else []
    if not items:
        return {}
    containers = (items[0].get("spec") or {}).get("containers") or [{}]
    return containers[0].get("securityContext") or {}


@register_scenario_check("runs_as_nonroot", 15)
def _runs_as_nonroot(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-006. Applied container must declare
    runAsNonRoot true and a numeric runAsUser >= 1000."""
    sc = _applied_container_sc(run_dir)
    ru = sc.get("runAsUser")
    ok = sc.get("runAsNonRoot") is True and isinstance(ru, int) and not isinstance(ru, bool) and ru >= 1000
    return ok, f"runAsNonRoot={sc.get('runAsNonRoot')} runAsUser={ru}"


@register_scenario_check("readonly_rootfs", 10)
def _readonly_rootfs(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-006."""
    sc = _applied_container_sc(run_dir)
    return sc.get("readOnlyRootFilesystem") is True, f"readOnlyRootFilesystem={sc.get('readOnlyRootFilesystem')}"


@register_scenario_check("no_priv_escalation", 10)
def _no_priv_escalation(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-006."""
    sc = _applied_container_sc(run_dir)
    return sc.get("allowPrivilegeEscalation") is False, f"allowPrivilegeEscalation={sc.get('allowPrivilegeEscalation')}"


@register_scenario_check("caps_dropped", 10)
def _caps_dropped(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-006. capabilities.drop must contain ALL and
    capabilities.add must be empty/absent."""
    caps = _applied_container_sc(run_dir).get("capabilities") or {}
    drop = caps.get("drop") or []
    add = caps.get("add") or []
    return ("ALL" in drop and not add), f"drop={drop} add={add}"


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


@register_scenario_check("config_applied", 15)
def _config_applied(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-007. The rendered Deployment must still source a
    container env var from a ConfigMap (configMapKeyRef), and GET / through the
    Service must return that config-sourced `tier` equal to the value the variant
    tree declares for the ConfigMap (config.tier in its values.yaml)."""
    dep = _load(run_dir, "rollout.json")
    containers = (((dep.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    ref_ok = any(
        (e.get("valueFrom") or {}).get("configMapKeyRef")
        for c in containers
        for e in (c.get("env") or [])
    )
    if not ref_ok:
        return False, "rendered Deployment has no env var sourced from a configMapKeyRef"

    import yaml  # local: keeps this addition append-only (yaml is not a module-wide import)
    try:
        vals = yaml.safe_load(
            (run_dir / "tree" / "charts" / "app" / "values.yaml").read_text(encoding="utf-8")
        )
    except OSError as e:
        return False, f"cannot read variant values.yaml: {e}"
    want = str(((vals or {}).get("config") or {}).get("tier", ""))
    if not want:
        return False, "variant values.yaml has empty config.tier"

    ok, parsed, detail = _http_get_json(namespace, release, "/")
    if not ok:
        return False, detail
    got = parsed.get("tier") if isinstance(parsed, dict) else None
    return got == want, f"GET / tier={got!r} want={want!r} ({detail})"


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


def _logs_are_json(run_dir, threshold: float = 0.95) -> bool:
    """True iff there is >= 1 stdout line and >= `threshold` of non-blank lines
    parse as JSON objects. Scenario-agnostic; feeds verdict.json `logs_are_json`."""
    lines = _log_lines(run_dir)
    if not lines:
        return False
    objs, total = _parse_json_logs(lines)
    return (len(objs) / total) >= threshold


def _stdout_line_count(run_dir) -> int:
    """Count of non-blank stdout lines in the run's collected pod logs."""
    return len(_log_lines(run_dir))


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


def _base_stdout_line_count():
    """The `stdout_line_count` recorded by the most recent base run
    (harness.run.run_variant), or None if it cannot be read."""
    from harness.paths import RUNS_DIR
    try:
        run_id = (RUNS_DIR / "last-base").read_text(encoding="utf-8").strip()
        meta = json.loads((RUNS_DIR / run_id / "meta.json").read_text(encoding="utf-8"))
        v = meta.get("stdout_line_count")
        return int(v) if isinstance(v, int) and not isinstance(v, bool) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


@register_scenario_check("structured_logs_ok", 35)
def _structured_logs_ok(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-008.

    PASS iff: >= 95 % of non-blank stdout lines are JSON objects; every object
    carries {ts, level, msg}; >= 1 object is an HTTP access record with
    {method, path, status} and 2xx status; a live GET /health through the
    Service returns 200; and this healthy run's stdout line count (fixed
    synthetic load) is >= the most recent base healthy run's — so "fixing" the
    logs by raising logLevel to suppress the access lines is caught.

    Records its measurements on ``meta['structured_logs']`` so run_scenario can
    surface the same numbers in verdict.json (the M9 contract's logs_are_json /
    stdout line counts) rather than a second, inconsistent snapshot."""
    base = _base_stdout_line_count()
    sl = {"logs_are_json": False, "json_ratio": 0.0, "access_records": 0,
          "stdout_line_count": None, "base_stdout_line_count": base}
    meta["structured_logs"] = sl

    lines = _log_lines(run_dir)
    if not lines:
        return False, "no stdout lines captured"
    objs, total = _parse_json_logs(lines)
    ratio = len(objs) / total
    sl["json_ratio"] = round(ratio, 4)
    sl["logs_are_json"] = ratio >= 0.95
    if ratio < 0.95:
        return False, f"{len(objs)}/{total} stdout lines are valid JSON (need >=95%)"
    bad = [o for o in objs if not {"ts", "level", "msg"} <= set(o)]
    if bad:
        return False, f"{len(bad)}/{len(objs)} JSON log objects lack required keys {{ts,level,msg}}"
    access = [
        o for o in objs
        if {"method", "path", "status"} <= set(o)
        and str(o.get("status")).isdigit() and 200 <= int(o["status"]) < 300
    ]
    sl["access_records"] = len(access)
    if not access:
        return False, "no structured 2xx HTTP access record on stdout"
    if _burst_health(namespace, release, 1) < 1:
        return False, "live GET /health through the Service did not return 200"
    if base is None:
        return False, "baseline stdout line count unavailable (run make e2e-base first)"
    try:
        this = measure_stdout_lines(namespace, release, 10)
    except RuntimeError as e:
        return False, f"could not measure stdout line count: {e}"
    sl["stdout_line_count"] = this
    if this < base:
        return False, f"stdout line count {this} < base healthy {base} (access logging suppressed?)"
    return True, (
        f"{len(objs)}/{total} JSON lines; {len(access)} access rec(s); "
        f"stdout_lines this={this} >= base={base}"
    )


@register_scenario_check("ci_gate_pass", 30)
def _ci_gate_pass(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-009. Runs the real M-BE ``scripts/ci.sh`` from
    inside the ephemeral scenario tree (pytest -q, helm lint, helm template
    smoke, docker build, :latest / unpinned-base grep) and PASSes iff it exits
    0. Writes the full transcript to ``run_dir/ci.log``. ``scripts/`` and
    ``config/`` are carried into the tree and are guarded byte-identical to
    base, so this scores the submitted tree, not the repo."""
    import sys as _sys
    from harness.paths import REPO_ROOT

    tree = run_dir / "tree"
    for rel in ("scripts/ci.sh", "scripts/lib.sh"):
        tf = tree / rel
        if (not tf.exists()) or tf.read_bytes() != (REPO_ROOT / rel).read_bytes():
            return False, f"tree {rel} is not byte-identical to base (tooling tampered)"

    env = dict(os.environ)
    env["PATH"] = tools.PATH
    env["PYTHON"] = _sys.executable          # the venv interpreter running the harness
    env["PYTHONPATH"] = str(tree)            # make `import app` resolve to the tree
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    exe = tools.which("bash") or "bash"
    try:
        cp = subprocess.run(
            [exe, "scripts/ci.sh"],
            cwd=str(tree),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=420,
        )
        out = (cp.stdout or "") + (cp.stderr or "")
        rc = cp.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") + "\n[timeout] scripts/ci.sh exceeded 420s\n"
        rc = 124
    (run_dir / "ci.log").write_text(out, encoding="utf-8")

    first_fail = next(
        (ln.strip() for ln in out.splitlines() if ln.startswith("FAILED ") or " FAILED" in ln),
        "",
    )
    tail = f" ({first_fail})" if first_fail else ""
    return rc == 0, f"scripts/ci.sh exit {rc}{tail}"


def evaluate(namespace: str, release: str, run_dir: pathlib.Path, meta: dict):
    results: list[dict] = []

    def add(cid: str, ok: bool, reason: str, na: bool = False) -> None:
        results.append(
            {
                "id": cid,
                "weight": CHECK_WEIGHTS[cid],
                "result": "NA" if na else ("PASS" if ok else "FAIL"),
                "reason": reason,
            }
        )

    # --- helm_release_ok ---
    hs = _load(run_dir, "helm-status.json")
    status = ((hs.get("info") or {}).get("status")) if isinstance(hs, dict) else None
    add("helm_release_ok", status == "deployed", f"helm release status={status!r}")

    # --- rollout_complete ---
    add(
        "rollout_complete",
        bool(meta.get("rollout_ok")),
        "kubectl rollout status " + ("succeeded" if meta.get("rollout_ok") else "failed/timed out"),
    )

    # --- deployment_ready ---
    dep = _load(run_dir, "rollout.json")
    spec_rep = (dep.get("spec") or {}).get("replicas")
    dst = dep.get("status") or {}
    ready = dst.get("readyReplicas", 0) or 0
    updated = dst.get("updatedReplicas", 0) or 0
    avail = dst.get("availableReplicas", 0) or 0
    ok = spec_rep is not None and ready == spec_rep and updated == spec_rep and avail == spec_rep
    add(
        "deployment_ready",
        ok,
        f"spec={spec_rep} ready={ready} updated={updated} available={avail}",
    )

    # --- pods_ready ---
    pods = _load(run_dir, "pods.json").get("items", []) if isinstance(_load(run_dir, "pods.json"), dict) else []
    threshold = int(VERSIONS.get("RESTART_THRESHOLD", "0"))
    total_restarts = 0
    all_ready = bool(pods)
    for p in pods:
        st = p.get("status") or {}
        for c in st.get("containerStatuses") or []:
            total_restarts += c.get("restartCount", 0)
        conds = {c["type"]: c["status"] for c in st.get("conditions") or []}
        if conds.get("Ready") != "True":
            all_ready = False
    add(
        "pods_ready",
        all_ready and total_restarts <= threshold,
        f"pods={len(pods)} all_ready={all_ready} restarts={total_restarts} (threshold {threshold})",
    )

    # --- endpoints_present ---
    eps = _load(run_dir, "endpoints.json")
    ready_addrs = 0
    for subset in eps.get("subsets") or []:
        ready_addrs += len(subset.get("addresses") or [])
    if ready_addrs == 0:
        slices = _load(run_dir, "endpointslices.json")
        for sl in slices.get("items") or []:
            for ep in sl.get("endpoints") or []:
                if (ep.get("conditions") or {}).get("ready"):
                    ready_addrs += len(ep.get("addresses") or [])
    add("endpoints_present", ready_addrs > 0, f"ready endpoint addresses={ready_addrs}")

    # --- http_health_ok ---
    if ready_addrs == 0:
        add("http_health_ok", False, "skipped probe: service has no ready endpoints")
    else:
        ok, reason = _http_health(namespace, release)
        add("http_health_ok", ok, reason)

    # --- no_bad_events (weight 0, report-only) ---
    bad = 0
    for e in _load(run_dir, "events.json").get("items") or []:
        if e.get("type") == "Warning" and e.get("reason") in _BAD_EVENT_REASONS:
            bad += e.get("count", 1)
    add("no_bad_events", bad == 0, f"warning events of interest={bad}")

    # --- scenario-registered checks (no-op unless the run activates them) ---
    weight_overrides = meta.get("weight_overrides") or {}
    for cid in meta.get("scenario_checks") or []:
        if cid not in _SCENARIO_CHECKS:
            continue
        default_w, fn = _SCENARIO_CHECKS[cid]
        weight = int(weight_overrides.get(cid, default_w))
        try:
            ok, reason = fn(run_dir=run_dir, namespace=namespace, release=release, meta=meta)
        except Exception as exc:  # noqa: BLE001 - a check crash is a FAIL, not a raise
            ok, reason = False, f"check error: {exc}"
        results.append(
            {"id": cid, "weight": weight, "result": "PASS" if ok else "FAIL", "reason": reason}
        )
    for c in results:
        if c["id"] in weight_overrides and c["id"] not in _SCENARIO_CHECKS:
            c["weight"] = int(weight_overrides[c["id"]])

    non_na = [c for c in results if c["result"] != "NA"]
    denom = sum(c["weight"] for c in non_na) or 1
    num = sum(c["weight"] for c in results if c["result"] == "PASS")
    score = round(num / denom * 100)

    (run_dir / "checks.json").write_text(
        json.dumps({"checks": results, "score": score}, indent=2)
    )
    return results, score


def is_healthy(checks: list[dict], score: int) -> bool:
    return score == 100 and all(
        c["result"] != "FAIL" for c in checks if c["weight"] > 0
    )
