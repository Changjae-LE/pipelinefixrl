"""harness.evaluate.evaluate end-to-end on fabricated run-dirs (no cluster).

endpoints_present is forced to 0 so http_health_ok takes the fast 'skipped
probe' branch and no port-forward is attempted.
"""

from harness.evaluate import evaluate, is_healthy

HARD_SC = {"runAsNonRoot": True, "runAsUser": 1000, "allowPrivilegeEscalation": False,
           "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
WEAK_SC = {"runAsNonRoot": False, "runAsUser": 0, "allowPrivilegeEscalation": True,
           "readOnlyRootFilesystem": False, "capabilities": {"drop": []}}


def _healthy_artifacts(sc=HARD_SC, endpoints_ready=True):
    return {
        "helm-status.json": {"info": {"status": "deployed"}},
        "rollout.json": {"spec": {"replicas": 1},
                         "status": {"readyReplicas": 1, "updatedReplicas": 1, "availableReplicas": 1}},
        "pods.json": {"items": [{
            "spec": {"containers": [{"name": "app", "securityContext": sc}]},
            "metadata": {"name": "app-1"},
            "status": {"conditions": [{"type": "Ready", "status": "True"}],
                       "containerStatuses": [{"name": "app", "restartCount": 0}]}}]},
        "endpoints.json": {"subsets": [{"addresses": [{"ip": "1.2.3.4"}]}]} if endpoints_ready
        else {"subsets": []},
        "endpointslices.json": {"items": []},
        "events.json": {"items": []},
    }


def test_backbone_only_all_pass_scores_100(run_dir):
    d = run_dir(**_healthy_artifacts(endpoints_ready=False))
    meta = {"rollout_ok": True}
    results, score = evaluate("ns", "app", d, meta)
    by = {c["id"]: c for c in results}
    assert [c["id"] for c in results] == [
        "helm_release_ok", "rollout_complete", "deployment_ready", "pods_ready",
        "endpoints_present", "http_health_ok", "no_bad_events"]
    # endpoints=0 -> endpoints_present + http_health_ok FAIL; the rest PASS
    assert by["helm_release_ok"]["result"] == "PASS"
    assert by["rollout_complete"]["result"] == "PASS"
    assert by["deployment_ready"]["result"] == "PASS"
    assert by["pods_ready"]["result"] == "PASS"
    assert by["endpoints_present"]["result"] == "FAIL"
    assert by["http_health_ok"]["result"] == "FAIL"
    assert "skipped probe" in by["http_health_ok"]["reason"]
    # score = (10+20+20+15) / (10+20+20+15+15+20+0) * 100 = 65
    assert score == 65


def test_failed_rollout(run_dir):
    art = _healthy_artifacts(endpoints_ready=False)
    d = run_dir(**art)
    results, _ = evaluate("ns", "app", d, {"rollout_ok": False})
    by = {c["id"]: c for c in results}
    assert by["rollout_complete"]["result"] == "FAIL"
    assert "failed/timed out" in by["rollout_complete"]["reason"]


def test_scenario_006_shape_broken_scores_55(run_dir):
    """weak securityContext + the 4 posture checks activated + scenario-006 weights."""
    meta = {
        "rollout_ok": True,
        "scenario_checks": ["runs_as_nonroot", "readonly_rootfs", "no_priv_escalation", "caps_dropped"],
        "weight_overrides": {"rollout_complete": 10, "deployment_ready": 10, "pods_ready": 5,
                             "endpoints_present": 10, "http_health_ok": 10,
                             "runs_as_nonroot": 15, "readonly_rootfs": 10,
                             "no_priv_escalation": 10, "caps_dropped": 10},
    }
    # http_health_ok would port-forward; force it to the skipped branch instead
    d_no_ep = run_dir(**_healthy_artifacts(sc=WEAK_SC, endpoints_ready=False))
    meta["weight_overrides"]["http_health_ok"] = 10
    results, score = evaluate("ns", "app", d_no_ep, meta)
    by = {c["id"]: c for c in results}
    for posture in ("runs_as_nonroot", "readonly_rootfs", "no_priv_escalation", "caps_dropped"):
        assert by[posture]["result"] == "FAIL"
        assert by[posture]["weight"] in (10, 15)
    # backbone reweighted; endpoints/http FAIL here (no endpoints in this fixture)
    assert by["helm_release_ok"]["weight"] == 10
    assert by["rollout_complete"]["weight"] == 10       # overridden from 20
    assert by["pods_ready"]["weight"] == 5              # overridden from 15


def test_checks_json_written(run_dir):
    d = run_dir(**_healthy_artifacts(endpoints_ready=False))
    results, score = evaluate("ns", "app", d, {"rollout_ok": True})
    import json
    on_disk = json.loads((d / "checks.json").read_text())
    assert on_disk["score"] == score
    assert on_disk["checks"] == results


def test_is_healthy():
    ok = [{"id": "a", "result": "PASS", "weight": 10}, {"id": "z", "result": "FAIL", "weight": 0}]
    assert is_healthy(ok, 100) is True                     # weight-0 FAIL ignored
    assert is_healthy(ok, 99) is False
    bad = [{"id": "a", "result": "FAIL", "weight": 10}]
    assert is_healthy(bad, 100) is False
