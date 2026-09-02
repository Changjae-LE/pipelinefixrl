"""service_ports_wired — the generic Service port-relationship check (offline).

Clause A: the Service exposes the published service port (SVC_PORT, 80).
Clause B: that port's targetPort corresponds to a declared containerPort.
Fabricated services.json/pods.json fixtures; no cluster."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from harness.checks import _SCENARIO_CHECKS
from harness.checks.backbone import _service_ports_wired

RELEASE = "app"


def _run_dir(tmp_path, svc_ports, container_ports, port_names=()):
    d = tmp_path / "run"
    d.mkdir(exist_ok=True)
    (d / "services.json").write_text(json.dumps({"items": [{
        "metadata": {"name": RELEASE},
        "spec": {"ports": svc_ports},
    }]}), encoding="utf-8")
    ports = [{"containerPort": p} for p in container_ports]
    for i, name in enumerate(port_names):
        if i < len(ports):
            ports[i]["name"] = name
    (d / "pods.json").write_text(json.dumps({"items": [{
        "metadata": {"name": "app-1"},
        "spec": {"containers": [{"name": "app", "ports": ports}]},
    }]}), encoding="utf-8")
    return d


def _check(run_dir):
    return _service_ports_wired(run_dir=run_dir, namespace="ns", release=RELEASE, meta={})


def test_registered_with_default_weight_15():
    assert _SCENARIO_CHECKS["service_ports_wired"][0] == 15


def test_correct_wiring_passes(tmp_path):
    ok, reason = _check(_run_dir(tmp_path, [{"port": 80, "targetPort": 8000}], [8000]))
    assert ok and "80" in reason and "8000" in reason


def test_h01_shape_targetport_mismatch_fails_clause_b(tmp_path):
    ok, reason = _check(_run_dir(tmp_path, [{"port": 80, "targetPort": 9090}], [8000]))
    assert not ok
    assert reason.startswith("clause B") and "9090" in reason and "8000" in reason


def test_h03_shape_published_port_absent_fails_clause_a(tmp_path):
    ok, reason = _check(_run_dir(tmp_path, [{"port": 8081, "targetPort": 8000}], [8000]))
    assert not ok
    assert reason.startswith("clause A") and "8081" in reason and "80" in reason


def test_named_targetport_resolves_through_container_port_names(tmp_path):
    ok, _ = _check(_run_dir(tmp_path, [{"port": 80, "targetPort": "http"}],
                            [8000], port_names=("http",)))
    assert ok
    ok, reason = _check(_run_dir(tmp_path, [{"port": 80, "targetPort": "https"}],
                                 [8000], port_names=("http",)))
    assert not ok and reason.startswith("clause B")


def test_missing_service_fails(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "services.json").write_text('{"items": []}', encoding="utf-8")
    (d / "pods.json").write_text('{"items": []}', encoding="utf-8")
    ok, reason = _check(d)
    assert not ok and "not found" in reason


def test_no_declared_container_ports_fails_clause_b(tmp_path):
    ok, reason = _check(_run_dir(tmp_path, [{"port": 80, "targetPort": 8000}], []))
    assert not ok and reason.startswith("clause B")
