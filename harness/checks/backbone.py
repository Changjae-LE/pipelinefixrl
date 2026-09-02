"""Backbone checks: Helm release, rollout, deployment/pod readiness, endpoints,
HTTP health, warning events, and Service<->Deployment selector wiring.

`run_backbone_checks` produces the same seven result rows (ids / weights /
results / reasons / order) the former inline block in evaluate() produced.
"""

from harness.checks import register_scenario_check
from harness.checks._util import _http_health, _load
from harness.paths import VERSIONS

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


def run_backbone_checks(run_dir, namespace, release, meta) -> list[dict]:
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

    return results


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


@register_scenario_check("service_ports_wired", 15)
def _service_ports_wired(*, run_dir, namespace, release, meta):
    """PASS iff (clause A) the Service exposes the benchmark's published
    service port (the SVC_PORT deploy contract, default 80 — the port every
    consumer, including the health probe, connects to) AND (clause B) that
    port's targetPort corresponds to a containerPort actually declared by the
    workload's pods. Relationship check over collected runtime objects only —
    no reference-answer material is consulted. Owning scenarios: held-out h01
    (targetPort mismatch → clause B) and h03 (published port absent →
    clause A)."""
    published = int(VERSIONS.get("SVC_PORT", "80"))
    svcs = _load(run_dir, "services.json")
    svc = next(
        (s for s in (svcs.get("items") or []) if (s.get("metadata") or {}).get("name") == release),
        None,
    )
    if svc is None:
        return False, f"Service {release!r} not found"
    ports = (svc.get("spec") or {}).get("ports") or []
    if not ports:
        return False, "clause A: Service declares no ports"
    svc_ports = [p.get("port") for p in ports]
    if published not in svc_ports:
        return False, (
            f"clause A: Service ports {svc_ports} do not expose the published "
            f"service port {published}"
        )

    cport_nums, cport_names = set(), set()
    for pod in _load(run_dir, "pods.json").get("items") or []:
        for c in ((pod.get("spec") or {}).get("containers") or []):
            for cp in c.get("ports") or []:
                if cp.get("containerPort") is not None:
                    cport_nums.add(cp["containerPort"])
                if cp.get("name"):
                    cport_names.add(cp["name"])
    if not cport_nums:
        return False, "clause B: no containerPort declared on any pod"

    bad = []
    for p in ports:
        if p.get("port") != published:
            continue
        tp = p.get("targetPort", p.get("port"))
        if isinstance(tp, int):
            if tp not in cport_nums:
                bad.append(tp)
        elif tp not in cport_names:
            bad.append(tp)
    if bad:
        return False, (
            f"clause B: Service targetPort {bad} does not correspond to a declared "
            f"containerPort {sorted(cport_nums)}"
        )
    return True, (
        f"published port {published} exposed; targetPort reaches containerPort "
        f"{sorted(cport_nums)}"
    )
