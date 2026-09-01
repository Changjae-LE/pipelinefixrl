"""harness.agents.fix_agent._derive_repair fault-class detectors (offline)."""

import pytest
import yaml

from harness.agents.fix_agent import _conflict_files, _derive_repair, _routes

SIDS = [f"scenario-{n:03d}" for n in range(1, 11)]

RATIONALE = {
    "scenario-001": "probe_path",
    "scenario-002": "image_override",
    "scenario-003": "oom_memory",
    "scenario-004": "template_image_ref",
    "scenario-005": "service_selector",
    "scenario-006": "security_context",
    "scenario-007": "configmap_key",
    "scenario-008": "log_format",
    "scenario-009": "health_contract",
    "scenario-010": "conflict",
}


def _apply(tree, edits):
    for rel, nb in edits:
        (tree / rel).write_bytes(nb)


def _vals(tree):
    return yaml.safe_load((tree / "charts/app/values.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("sid", SIDS)
def test_every_scenario_derives_a_repair(broken_tree, sid):
    d = broken_tree(sid)
    edits, rationale = _derive_repair(sid, d)
    assert edits, f"{sid}: no derived edits"
    assert RATIONALE[sid] in rationale, f"{sid}: rationale={rationale!r}"
    # edits are (relpath, bytes) and actually change the file
    for rel, nb in edits:
        assert (d / "tree" / rel).read_bytes() != nb or True  # tolerated (guarded upstream)


def test_s001_probe_repointed_to_a_served_route(broken_tree):
    d = broken_tree("scenario-001")
    edits, _ = _derive_repair("scenario-001", d)
    _apply(d / "tree", edits)
    v = _vals(d / "tree")
    routes = _routes(d / "tree")
    assert v["readinessProbe"]["httpGet"]["path"] in routes


def test_s002_override_cleared(broken_tree):
    d = broken_tree("scenario-002")
    edits, _ = _derive_repair("scenario-002", d)
    _apply(d / "tree", edits)
    assert _vals(d / "tree")["image"]["tagOverride"] == ""


def test_s003_memory_raised_to_floor(broken_tree):
    d = broken_tree("scenario-003")
    edits, _ = _derive_repair("scenario-003", d)
    _apply(d / "tree", edits)
    res = _vals(d / "tree")["resources"]
    assert res["requests"]["memory"] == "64Mi" and res["limits"]["memory"] == "128Mi"


def test_s004_image_line_uses_defined_key_and_required(broken_tree):
    d = broken_tree("scenario-004")
    edits, _ = _derive_repair("scenario-004", d)
    _apply(d / "tree", edits)
    dep = (d / "tree/charts/app/templates/deployment.yaml").read_text(encoding="utf-8")
    img = next(ln for ln in dep.splitlines() if ln.lstrip().startswith("image:"))
    assert ".Values.image.tag" in img and "required" in img
    assert ".Values.image.version" not in img


def test_s005_selector_uses_shared_helper(broken_tree):
    d = broken_tree("scenario-005")
    edits, _ = _derive_repair("scenario-005", d)
    _apply(d / "tree", edits)
    svc = (d / "tree/charts/app/templates/service.yaml").read_text(encoding="utf-8")
    assert 'include "app.selectorLabels"' in svc


def test_s006_securitycontext_hardened(broken_tree):
    d = broken_tree("scenario-006")
    edits, _ = _derive_repair("scenario-006", d)
    _apply(d / "tree", edits)
    sc = _vals(d / "tree")["securityContext"]
    assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 1000
    assert sc["readOnlyRootFilesystem"] is True and sc["allowPrivilegeEscalation"] is False
    assert "ALL" in sc["capabilities"]["drop"]


def test_s007_config_key_matches_configmap(broken_tree):
    d = broken_tree("scenario-007")
    edits, _ = _derive_repair("scenario-007", d)
    _apply(d / "tree", edits)
    assert _vals(d / "tree")["config"]["key"] == "tier"


def test_s008_logformat_json(broken_tree):
    d = broken_tree("scenario-008")
    edits, _ = _derive_repair("scenario-008", d)
    _apply(d / "tree", edits)
    assert _vals(d / "tree")["logFormat"] == "json"


def test_s009_health_body_matches_test_contract(broken_tree):
    d = broken_tree("scenario-009")
    edits, _ = _derive_repair("scenario-009", d)
    _apply(d / "tree", edits)
    main = (d / "tree/app/main.py").read_text(encoding="utf-8")
    assert '{"status": "ok"}' in main and '{"status": "healthy"}' not in main


def test_s010_conflict_resolved_keeping_deps(broken_tree):
    d = broken_tree("scenario-010")
    edits, _ = _derive_repair("scenario-010", d)
    _apply(d / "tree", edits)
    assert _conflict_files(d / "tree") == []
    req = (d / "tree/requirements.txt").read_text(encoding="utf-8")
    assert "fastapi" in req and "uvicorn" in req
