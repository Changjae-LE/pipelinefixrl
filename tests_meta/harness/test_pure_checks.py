"""Every scenario check that needs no cluster / HTTP, against fabricated run-dirs."""

from harness.checks.backbone import _service_selects_pods
from harness.checks.build import _git_tree_resolved, _image_build_ok, _image_pull_ok, _no_oomkill
from harness.checks.security import (
    _caps_dropped,
    _no_priv_escalation,
    _readonly_rootfs,
    _runs_as_nonroot,
)

HARD_SC = {"runAsNonRoot": True, "runAsUser": 1000, "allowPrivilegeEscalation": False,
           "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
WEAK_SC = {"runAsNonRoot": False, "runAsUser": 0, "allowPrivilegeEscalation": True,
           "readOnlyRootFilesystem": False, "capabilities": {"drop": []}}


def _pods_sc(sc):
    return {"items": [{"spec": {"containers": [{"name": "app", "securityContext": sc}]}}]}


# --- image_pull_ok --------------------------------------------------------
def test_image_pull_ok_clean(run_dir):
    d = run_dir(**{"pods.json": {"items": [{"status": {"containerStatuses": [
        {"name": "app", "state": {"running": {}}}]}}]}})
    ok, why = _image_pull_ok(run_dir=d, namespace="n", release="app", meta={})
    assert ok and "no image-acquisition" in why


def test_image_pull_ok_errimageneverpull(run_dir):
    d = run_dir(**{"pods.json": {"items": [{"status": {"containerStatuses": [
        {"name": "app", "state": {"waiting": {"reason": "ErrImageNeverPull"}}}]}}]}})
    ok, why = _image_pull_ok(run_dir=d, namespace="n", release="app", meta={})
    assert not ok and "ErrImageNeverPull" in why


# --- no_oomkill --------------------------------------------------------
def test_no_oomkill_clean(run_dir):
    d = run_dir(**{"pods.json": {"items": [{"status": {"containerStatuses": [
        {"name": "app", "restartCount": 0}]}}]}})
    ok, why = _no_oomkill(run_dir=d, namespace="n", release="app", meta={})
    assert ok and "no OOMKill" in why


def test_no_oomkill_detects_137(run_dir):
    d = run_dir(**{"pods.json": {"items": [{"status": {"containerStatuses": [
        {"name": "app", "restartCount": 3,
         "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}}}]}}]}})
    ok, why = _no_oomkill(run_dir=d, namespace="n", release="app", meta={})
    assert not ok and "OOMKilled" in why and "137" in why


def test_no_oomkill_restart_threshold(run_dir):
    d = run_dir(**{"pods.json": {"items": [{"status": {"containerStatuses": [
        {"name": "app", "restartCount": 5}]}}]}})
    ok, why = _no_oomkill(run_dir=d, namespace="n", release="app", meta={})
    assert not ok and "restartCount total 5 > threshold 0" in why


# --- security posture --------------------------------------------------------
def test_security_checks_hardened(run_dir):
    d = run_dir(**{"pods.json": _pods_sc(HARD_SC)})
    kw = dict(run_dir=d, namespace="n", release="app", meta={})
    assert _runs_as_nonroot(**kw)[0]
    assert _readonly_rootfs(**kw)[0]
    assert _no_priv_escalation(**kw)[0]
    assert _caps_dropped(**kw)[0]


def test_security_checks_weak(run_dir):
    d = run_dir(**{"pods.json": _pods_sc(WEAK_SC)})
    kw = dict(run_dir=d, namespace="n", release="app", meta={})
    assert not _runs_as_nonroot(**kw)[0]
    assert not _readonly_rootfs(**kw)[0]
    assert not _no_priv_escalation(**kw)[0]
    assert not _caps_dropped(**kw)[0]


def test_runs_as_nonroot_rejects_bool_uid(run_dir):
    d = run_dir(**{"pods.json": _pods_sc({"runAsNonRoot": True, "runAsUser": True})})
    assert _runs_as_nonroot(run_dir=d, namespace="n", release="app", meta={})[0] is False


def test_caps_dropped_rejects_added_cap(run_dir):
    d = run_dir(**{"pods.json": _pods_sc(
        {"capabilities": {"drop": ["ALL"], "add": ["NET_ADMIN"]}})})
    assert _caps_dropped(run_dir=d, namespace="n", release="app", meta={})[0] is False


# --- service_selects_pods --------------------------------------------------------
def test_service_selects_pods_match(run_dir):
    sel = {"app.kubernetes.io/name": "app", "app.kubernetes.io/instance": "app"}
    d = run_dir(**{
        "services.json": {"items": [{"metadata": {"name": "app"}, "spec": {"selector": sel}}]},
        "rollout.json": {"spec": {"selector": {"matchLabels": sel}}},
        "pods.json": {"items": [{"metadata": {"name": "p1", "labels": sel},
                                 "status": {"conditions": [{"type": "Ready", "status": "True"}]}}]},
        "endpointslices.json": {"items": [{"metadata": {"labels": {"kubernetes.io/service-name": "app"}},
            "endpoints": [{"conditions": {"ready": True},
                           "targetRef": {"kind": "Pod", "name": "p1"}}]}]},
    })
    ok, why = _service_selects_pods(run_dir=d, namespace="n", release="app", meta={})
    assert ok and "selector matches Deployment" in why


def test_service_selects_pods_mismatch(run_dir):
    d = run_dir(**{
        "services.json": {"items": [{"metadata": {"name": "app"},
                                     "spec": {"selector": {"app.kubernetes.io/name": "web"}}}]},
        "rollout.json": {"spec": {"selector": {"matchLabels": {"app.kubernetes.io/name": "app"}}}},
        "pods.json": {"items": []},
        "endpointslices.json": {"items": []},
    })
    ok, why = _service_selects_pods(run_dir=d, namespace="n", release="app", meta={})
    assert not ok and "!= Deployment matchLabels" in why


def test_service_selects_pods_empty_selector(run_dir):
    d = run_dir(**{"services.json": {"items": [{"metadata": {"name": "app"}, "spec": {"selector": {}}}]}})
    ok, why = _service_selects_pods(run_dir=d, namespace="n", release="app", meta={})
    assert not ok and "selector is empty" in why


# --- image_build_ok / git_tree_resolved --------------------------------------
def test_image_build_ok_true(run_dir):
    ok, why = _image_build_ok(run_dir=run_dir(), namespace="n", release="app", meta={"build_ok": True})
    assert ok and "exit 0" in why


def test_image_build_ok_false_with_logtail(run_dir):
    d = run_dir(**{"build.log": "RUN pip install ...\nERROR: bad thing\nreturned a non-zero code: 1\n"})
    ok, why = _image_build_ok(run_dir=d, namespace="n", release="app", meta={"build_ok": False})
    assert not ok and "non-zero" in why and "::" in why


def test_git_tree_resolved_clean(tmp_path):
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "requirements.txt").write_text("fastapi>=0.110\n", encoding="utf-8")
    ok, why = _git_tree_resolved(run_dir=tmp_path, namespace="n", release="app", meta={})
    assert ok and why == "clean"


def test_git_tree_resolved_detects_markers(tmp_path):
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "requirements.txt").write_text(
        "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n", encoding="utf-8")
    ok, why = _git_tree_resolved(run_dir=tmp_path, namespace="n", release="app", meta={})
    assert not ok and "requirements.txt" in why
