"""Repair primitives + deterministic composition engine (offline).

Covers: the Evidence signal layer; the general port/scheduling relationships
added in Improvement 2 (probe port vs containerPort, Service targetPort vs
containerPort, Unschedulable resource clamp); and the composition semantics —
two compatible edits to one file, duplicate collapse, explicit conflicts."""

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import harness.agents.primitives as prims
from harness.patching import _copy_base_tree


def _base(tmp_path):
    tree = tmp_path / "tree"
    _copy_base_tree(tree)
    return tree


def _vals(tree):
    return yaml.safe_load((tree / "charts/app/values.yaml").read_text(encoding="utf-8"))


def _set_values_text(tree, old, new):
    vp = tree / "charts/app/values.yaml"
    txt = vp.read_text(encoding="utf-8")
    assert old in txt, f"fixture edit target {old!r} not found"
    vp.write_text(txt.replace(old, new), encoding="utf-8", newline="\n")


def _ev(tmp_path, **files):
    d = tmp_path / "evrun"
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name.replace("__", ".")).write_text(text, encoding="utf-8")
    return prims.Evidence(d)


# --- Evidence: workload-state signal layer --------------------------------
def test_evidence_empty_run_dir_has_no_signals(tmp_path):
    ev = prims.Evidence(tmp_path / "nowhere")
    assert ev.signals == set() and ev.sources == [] and ev.failed_checks == []


def test_evidence_maps_event_text_to_typed_signals(tmp_path):
    ev = _ev(tmp_path, events__txt="Warning FailedScheduling ... Insufficient memory")
    assert "unschedulable" in ev.signals
    ev2 = _ev(tmp_path / "b", pods__json='{"reason": "OOMKilled", "exitCode": 137}')
    assert "oom" in ev2.signals
    ev3 = _ev(tmp_path / "c", events__txt="Readiness probe failed: connect: connection refused")
    assert {"probe_failure", "connection_refused"} <= ev3.signals


def test_evidence_reads_failed_checks_as_refinement_signal(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "checks.json").write_text(
        '{"checks": [{"id": "http_health_ok", "result": "FAIL", "reason": "no ready endpoints"},'
        ' {"id": "pods_ready", "result": "PASS", "reason": "ok"}], "score": 55}',
        encoding="utf-8")
    ev = prims.Evidence(d)
    assert ev.failed_checks == [("http_health_ok", "no ready endpoints")]
    assert "checks.json" in ev.sources


# --- general relationships: probe port vs containerPort -------------------
def test_probe_port_realigned_to_container_port(tmp_path):
    tree = _base(tmp_path)
    _set_values_text(tree, "    port: 8000\n  initialDelaySeconds: 2",
                     "    port: 9999\n  initialDelaySeconds: 2")
    fs = prims.p_http_contract(tree, _ev(tmp_path))
    port_f = [f for f in fs if f.diagnosis == "probe_port"]
    assert len(port_f) == 1
    (rel, nb), = port_f[0].edits
    (tree / rel).write_bytes(nb)
    v = _vals(tree)
    assert v["readinessProbe"]["httpGet"]["port"] == v["containerPort"] == 8000
    assert v["livenessProbe"]["httpGet"]["port"] == 8000


def test_probe_port_no_finding_when_aligned(tmp_path):
    tree = _base(tmp_path)
    assert [f for f in prims.p_http_contract(tree, _ev(tmp_path))
            if f.diagnosis == "probe_port"] == []


# --- general relationships: Service targetPort vs containerPort -----------
def test_service_target_port_realigned(tmp_path):
    tree = _base(tmp_path)
    _set_values_text(tree, "targetPort: 8000", "targetPort: 9090")
    fs = prims.p_service_wiring(tree, _ev(tmp_path))
    tp = [f for f in fs if f.diagnosis == "service_target_port"]
    assert len(tp) == 1
    (rel, nb), = tp[0].edits
    (tree / rel).write_bytes(nb)
    v = _vals(tree)
    assert v["service"]["targetPort"] == v["containerPort"] == 8000
    # the cluster-facing service port is untouched
    assert v["service"]["port"] == 80


def test_service_target_port_no_finding_when_aligned(tmp_path):
    tree = _base(tmp_path)
    assert [f for f in prims.p_service_wiring(tree, _ev(tmp_path))
            if f.diagnosis == "service_target_port"] == []


# --- general relationships: Unschedulable resource clamp ------------------
def test_unschedulable_request_clamped_with_signal(tmp_path):
    tree = _base(tmp_path)
    _set_values_text(tree, "requests:\n    cpu: 50m\n    memory: 64Mi",
                     "requests:\n    cpu: 50m\n    memory: 2Gi")
    ev = _ev(tmp_path, events__txt="0/1 nodes are available: 1 Insufficient memory. FailedScheduling")
    fs = prims.p_runtime_constraints(tree, ev)
    un = [f for f in fs if f.diagnosis == "unschedulable_resources"]
    assert len(un) == 1
    (rel, nb), = un[0].edits
    (tree / rel).write_bytes(nb)
    res = _vals(tree)["resources"]
    assert res["requests"]["memory"] == "64Mi" and res["limits"]["memory"] == "128Mi"
    assert res["requests"]["cpu"] == "50m" and res["limits"]["cpu"] == "250m"


def test_absurd_request_clamped_even_without_signal(tmp_path):
    tree = _base(tmp_path)
    _set_values_text(tree, "requests:\n    cpu: 50m\n    memory: 64Mi",
                     "requests:\n    cpu: 50m\n    memory: 64Gi")
    fs = prims.p_runtime_constraints(tree, _ev(tmp_path))
    assert [f.diagnosis for f in fs].count("unschedulable_resources") == 1


def test_sane_resources_produce_no_constraint_finding(tmp_path):
    tree = _base(tmp_path)
    fs = prims.p_runtime_constraints(tree, _ev(tmp_path))
    assert [f for f in fs if f.diagnosis in ("oom_memory", "unschedulable_resources")] == []


# --- composition: multi-fault trees ---------------------------------------
def test_two_faults_in_one_file_compose(tmp_path):
    """A tree carrying two independent faults in values.yaml gets both repairs
    in one composed candidate — two primitives editing the same file."""
    tree = _base(tmp_path)
    _set_values_text(tree, "path: /health\n", "path: /health2\n")   # probe path fault
    _set_values_text(tree, "logFormat: json", "logFormat: plain")   # log format fault
    res = prims.compose(tree, _ev(tmp_path))
    diags = {a["diagnosis"] for a in res.applied}
    assert {"probe_path", "log_format"} <= diags
    assert res.conflicts == []
    v = _vals(tree)
    assert v["readinessProbe"]["httpGet"]["path"] == "/health"
    assert v["logFormat"] == "json"
    assert res.changed == {"charts/app/values.yaml"}


def test_compose_is_a_noop_on_the_healthy_base_tree(tmp_path):
    tree = _base(tmp_path)
    res = prims.compose(tree, _ev(tmp_path))
    assert res.applied == [] and res.changed == set()


# --- composition semantics with synthetic primitives ----------------------
def _prim(name, diagnosis, transform):
    def _p(tree, ev):
        f = tree / "f.txt"
        cur = f.read_text(encoding="utf-8")
        new = transform(cur)
        if new == cur:
            return []
        return [prims.Finding(primitive=name, diagnosis=diagnosis,
                              rationale=f"{name} edit", edits=[("f.txt", new.encode("utf-8"))])]
    return _p


def _mini_tree(tmp_path, text):
    tree = tmp_path / "mini"
    tree.mkdir()
    (tree / "f.txt").write_text(text, encoding="utf-8")
    return tree


def test_compatible_edits_to_one_file_both_apply(tmp_path):
    tree = _mini_tree(tmp_path, "alpha\nbeta\ngamma\n")
    a = _prim("prim_a", "fix_alpha", lambda t: t.replace("alpha", "ALPHA"))
    b = _prim("prim_b", "fix_gamma", lambda t: t.replace("gamma", "GAMMA"))
    res = prims.compose(tree, _ev(tmp_path), primitives=[a, b])
    assert (tree / "f.txt").read_text(encoding="utf-8") == "ALPHA\nbeta\nGAMMA\n"
    assert [x["primitive"] for x in res.applied] == ["prim_a", "prim_b"]
    assert res.conflicts == [] and res.duplicates == []


def test_duplicate_identical_edits_collapse(tmp_path):
    tree = _mini_tree(tmp_path, "alpha\n")
    a = _prim("prim_a", "fix", lambda t: t.replace("alpha", "ALPHA"))

    def b(tree_, ev_):  # independently proposes the exact same resulting bytes
        return [prims.Finding(primitive="prim_b", diagnosis="fix_again",
                              rationale="same fix", edits=[("f.txt", b"ALPHA\n")])]
    res = prims.compose(tree, _ev(tmp_path), primitives=[a, b])
    assert (tree / "f.txt").read_text(encoding="utf-8") == "ALPHA\n"
    assert len(res.applied) == 1 and res.applied[0]["primitive"] == "prim_a"
    assert res.duplicates == [{"primitive": "prim_b", "diagnosis": "fix_again",
                               "file": "f.txt"}]
    assert res.conflicts == []


def test_conflicting_edit_to_same_region_is_recorded_and_skipped(tmp_path):
    tree = _mini_tree(tmp_path, "alpha\n")
    a = _prim("prim_a", "set_foo", lambda t: t.replace("alpha", "foo"))
    b = _prim("prim_b", "set_bar", lambda t: t.replace("foo", "bar"))  # rewrites a's line
    res = prims.compose(tree, _ev(tmp_path), primitives=[a, b])
    # the earlier primitive's edit is kept, the later one is an explicit conflict
    assert (tree / "f.txt").read_text(encoding="utf-8") == "foo\n"
    assert res.conflicts == [{"file": "f.txt", "kept": "prim_a",
                              "skipped": "prim_b", "skipped_diagnosis": "set_bar"}]
    assert [x["primitive"] for x in res.applied] == ["prim_a"]


def test_primitive_crash_is_contained(tmp_path):
    tree = _mini_tree(tmp_path, "alpha\n")

    def boom(tree_, ev_):
        raise RuntimeError("primitive exploded")
    a = _prim("prim_a", "fix", lambda t: t.replace("alpha", "ALPHA"))
    res = prims.compose(tree, _ev(tmp_path), primitives=[boom, a])
    assert (tree / "f.txt").read_text(encoding="utf-8") == "ALPHA\n"
    assert len(res.applied) == 1
