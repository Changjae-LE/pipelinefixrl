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
