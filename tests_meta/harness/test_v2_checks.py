"""v2 benchmark-side checks (offline): pod_security_baseline, workload_capacity.

Fabricated collected-artifact fixtures; no cluster. Both checks must report the
observed state and must never emit an imperative repair instruction."""

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from harness.checks import _SCENARIO_CHECKS
from harness.checks.backbone import _workload_capacity
from harness.checks.security import _pod_security_baseline

RELEASE = "app"
# a check may state the violated fact; it may never hand over the answer
_IMPERATIVE = re.compile(
    r"\b(set|change|restore|add|use|should be|must be set to)\b\s+\S*\s*(to|back)\b",
    re.IGNORECASE)


def _run(d, fn):
    return fn(run_dir=d, namespace="ns", release=RELEASE, meta={})


# --- registration ---------------------------------------------------------
def test_both_checks_are_registered_with_their_weights():
    assert _SCENARIO_CHECKS["pod_security_baseline"][0] == 15
    assert _SCENARIO_CHECKS["workload_capacity"][0] == 15


# --- pod_security_baseline ------------------------------------------------
def _pods(tmp_path, pod_sc):
    d = tmp_path / "run"
    d.mkdir(exist_ok=True)
    (d / "pods.json").write_text(json.dumps({"items": [{
        "metadata": {"name": "app-1"},
        "spec": {"securityContext": pod_sc, "containers": [{"name": "app"}]},
    }]}), encoding="utf-8")
    return d


def test_pod_security_baseline_passes_on_runtimedefault(tmp_path):
    ok, reason = _run(_pods(tmp_path, {"seccompProfile": {"type": "RuntimeDefault"}}),
                      _pod_security_baseline)
    assert ok and "1 pod" in reason


def test_pod_security_baseline_accepts_localhost_profile(tmp_path):
    ok, _ = _run(_pods(tmp_path, {"seccompProfile": {"type": "Localhost"}}),
                 _pod_security_baseline)
    assert ok


def test_pod_security_baseline_fails_when_pod_context_empty(tmp_path):
    ok, reason = _run(_pods(tmp_path, {}), _pod_security_baseline)
    assert not ok
    assert "seccompProfile" in reason and "None" in reason
    assert not _IMPERATIVE.search(reason)


def test_pod_security_baseline_fails_on_unconfined(tmp_path):
    ok, reason = _run(_pods(tmp_path, {"seccompProfile": {"type": "Unconfined"}}),
                      _pod_security_baseline)
    assert not ok and "Unconfined" in reason


def test_pod_security_baseline_fails_with_no_pods(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    (d / "pods.json").write_text('{"items": []}', encoding="utf-8")
    ok, reason = _run(d, _pod_security_baseline)
    assert not ok and "no pods" in reason


# --- workload_capacity ----------------------------------------------------
def _rollout(tmp_path, desired, ready):
    d = tmp_path / "run"
    d.mkdir(exist_ok=True)
    body = {"spec": {}, "status": {}}
    if desired is not None:
        body["spec"]["replicas"] = desired
    body["status"]["readyReplicas"] = ready
    (d / "rollout.json").write_text(json.dumps(body), encoding="utf-8")
    return d


def test_workload_capacity_passes_when_desired_ready(tmp_path):
    ok, reason = _run(_rollout(tmp_path, 1, 1), _workload_capacity)
    assert ok and "desired=1 ready=1" in reason


def test_workload_capacity_fails_on_zero_desired(tmp_path):
    # the vh06 shape: deployment_ready would pass vacuously (ready==desired==0)
    ok, reason = _run(_rollout(tmp_path, 0, 0), _workload_capacity)
    assert not ok
    assert "no capacity" in reason and "desired replicas=0" in reason
    assert not _IMPERATIVE.search(reason)


def test_workload_capacity_fails_when_ready_below_desired(tmp_path):
    ok, reason = _run(_rollout(tmp_path, 3, 1), _workload_capacity)
    assert not ok and "ready replicas 1 != desired 3" in reason


def test_workload_capacity_fails_when_no_replica_count(tmp_path):
    ok, reason = _run(_rollout(tmp_path, None, 0), _workload_capacity)
    assert not ok and "no replica count" in reason


def test_neither_check_is_scenario_id_specific():
    import inspect
    for fn in (_pod_security_baseline, _workload_capacity):
        src = inspect.getsource(fn)
        assert not re.search(r"\bvh0\d\b|\bh0\d\b|scenario-\d", src, re.I)
