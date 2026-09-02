"""held-out-v2 scenario contracts (offline): loader resolution, definition
shape, break+golden byte-identical round-trip, per-fault structural assertions,
and the v2 novelty-composition invariant. No Docker/kind/K8s/network/patch."""

import pathlib
import re
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import _diffapply
from harness import scenario as scenmod
from harness.anticheat import _ANTICHEAT_RULES
from harness.checks import _SCENARIO_CHECKS
from harness.patching import _copy_base_tree, tree_matches_base

REPO = pathlib.Path(__file__).resolve().parents[2]
V2 = REPO / "harness" / "scenarios" / "held-out-v2"
VIDS = [f"vh0{i}" for i in range(1, 9)]
TYPE_A, TYPE_B, COMPOUND = ["vh01", "vh02", "vh03"], ["vh04", "vh05", "vh06"], ["vh07", "vh08"]


def _cfg(vid):
    return yaml.safe_load((V2 / vid / "scenario.yaml").read_text(encoding="utf-8"))


def _broken(tmp_path, vid):
    tree = tmp_path / vid
    _copy_base_tree(tree)
    _diffapply.apply(tree, V2 / vid / "break.patch")
    return tree


def _vals(tree):
    return yaml.safe_load((tree / "charts/app/values.yaml").read_text(encoding="utf-8"))


# --- loader ---------------------------------------------------------------
@pytest.mark.parametrize("vid", VIDS)
def test_v2_ids_resolve_into_held_out_v2(vid):
    assert scenmod._scenario_dir(vid) == scenmod.SCENARIOS_DIR / "held-out-v2" / vid


def test_v1_and_development_resolution_is_unchanged():
    assert scenmod._scenario_dir("scenario-001") == scenmod.SCENARIOS_DIR / "scenario-001"
    assert scenmod._scenario_dir("h01") == scenmod.SCENARIOS_DIR / "held-out" / "h01"
    assert not scenmod._scenario_dir("scenario-999").exists()


# --- definition shape -----------------------------------------------------
@pytest.mark.parametrize("vid", VIDS)
def test_definition_is_complete_and_registry_compatible(vid):
    for name in ("scenario.yaml", "task.md", "break.patch", "golden.patch"):
        assert (V2 / vid / name).is_file(), f"{vid}: missing {name}"
    cfg = _cfg(vid)
    assert cfg["id"] == vid
    assert cfg["patches"] == {"break": "break.patch", "golden": "golden.patch"}
    ev = cfg["evaluation"]
    # fail-closed: every declared check / rule must exist in its registry
    assert set(ev.get("checks") or []) <= set(_SCENARIO_CHECKS)
    assert set(ev.get("anticheat") or {}) <= set(_ANTICHEAT_RULES)
    exp = ev["expect"]
    assert exp["golden"]["score_min"] == 100 and exp["golden"]["anticheat_clean"] is True
    assert exp["broken"]["score_max"] < 100


@pytest.mark.parametrize("vid", VIDS)
def test_activated_weights_total_one_hundred(vid):
    ev = _cfg(vid)["evaluation"]
    defaults = {"helm_release_ok": 10, "rollout_complete": 20, "deployment_ready": 20,
                "pods_ready": 15, "endpoints_present": 15, "http_health_ok": 20,
                "no_bad_events": 0}
    weights = dict(defaults)
    for cid in ev.get("checks") or []:
        weights[cid] = _SCENARIO_CHECKS[cid][0]
    weights.update(ev.get("weights") or {})
    assert sum(weights.values()) == 100, f"{vid}: weights total {sum(weights.values())}"


@pytest.mark.parametrize("vid", VIDS)
def test_task_file_does_not_hand_over_the_repair_value(vid):
    task = (V2 / vid / "task.md").read_text(encoding="utf-8")
    golden = (V2 / vid / "golden.patch").read_text(encoding="utf-8")
    added = [ln[1:].strip() for ln in golden.splitlines()
             if ln.startswith("+") and not ln.startswith("+++")]
    for line in added:
        assert line not in task, f"{vid}: task.md leaks a golden line: {line!r}"


# --- compose / apply identity --------------------------------------------
@pytest.mark.parametrize("vid", VIDS)
def test_break_plus_golden_round_trips_to_base(vid, tmp_path):
    tree = _broken(tmp_path, vid)
    assert tree_matches_base(tree) != [], f"{vid}: break.patch changed nothing"
    _diffapply.apply(tree, V2 / vid / "golden.patch")
    assert tree_matches_base(tree) == [], f"{vid}: break+golden != base"


def test_each_scenario_mutates_its_intended_surface(tmp_path):
    expected = {
        "vh01": ["charts/app/values.yaml"],
        "vh02": ["charts/app/templates/deployment.yaml"],
        "vh03": ["charts/app/values.yaml"],
        "vh04": ["docker/Dockerfile"],
        "vh05": ["charts/app/values.yaml"],
        "vh06": ["charts/app/values.yaml"],
        "vh07": ["charts/app/values.yaml"],
        "vh08": ["charts/app/values.yaml", "requirements.txt"],
    }
    for vid, files in expected.items():
        assert sorted(tree_matches_base(_broken(tmp_path, vid))) == sorted(files), vid


# --- per-fault structural assertions -------------------------------------
def test_vh01_breaks_only_the_liveness_probe_port(tmp_path):
    v = _vals(_broken(tmp_path, "vh01"))
    assert v["livenessProbe"]["httpGet"]["port"] == 9000
    assert v["readinessProbe"]["httpGet"]["port"] == v["containerPort"] == 8000
    assert _cfg("vh01")["evaluation"]["settle_seconds"] >= 35  # past failureThreshold


def test_vh02_image_line_references_a_defined_but_wrong_key(tmp_path):
    dep = (_broken(tmp_path, "vh02") / "charts/app/templates/deployment.yaml").read_text(
        encoding="utf-8")
    img = next(ln for ln in dep.splitlines() if ln.lstrip().startswith("image:"))
    assert ".Values.image.pullPolicy" in img          # defined key...
    assert ".Values.image.tag" not in img             # ...but not the tag contract
    assert "required" not in img


def test_vh03_breaks_only_the_capability_set(tmp_path):
    sc = _vals(_broken(tmp_path, "vh03"))["securityContext"]
    assert sc["capabilities"]["drop"] == []
    assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 1000
    assert sc["readOnlyRootFilesystem"] is True and sc["allowPrivilegeEscalation"] is False


def test_vh04_moves_the_listener_not_the_chart(tmp_path):
    tree = _broken(tmp_path, "vh04")
    df = (tree / "docker/Dockerfile").read_text(encoding="utf-8")
    assert '"--port", "9000"' in df
    assert "EXPOSE 8000" in df          # the image still documents the contract
    v = _vals(tree)                     # the whole chart side stays self-consistent
    assert v["containerPort"] == 8000
    assert v["service"]["targetPort"] == 8000 and v["service"]["port"] == 80
    assert v["readinessProbe"]["httpGet"]["port"] == 8000
    assert "charts" in _cfg("vh04")["evaluation"]["anticheat"]["frozen_paths_intact"]


def test_vh05_removes_only_the_pod_level_context(tmp_path):
    v = _vals(_broken(tmp_path, "vh05"))
    assert v["podSecurityContext"] == {}
    assert "ALL" in v["securityContext"]["capabilities"]["drop"]  # container intact


def test_vh06_scales_to_zero(tmp_path):
    assert _vals(_broken(tmp_path, "vh06"))["replicaCount"] == 0


def test_vh07_carries_two_independent_faults(tmp_path):
    v = _vals(_broken(tmp_path, "vh07"))
    assert v["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert v["securityContext"]["capabilities"]["drop"] == []
    assert v["livenessProbe"]["httpGet"]["path"] == "/health"   # untouched


def test_vh08_hides_a_port_fault_behind_a_build_failure(tmp_path):
    tree = _broken(tmp_path, "vh08")
    req = (tree / "requirements.txt").read_text(encoding="utf-8")
    assert "<<<<<<<" in req and ">>>>>>>" in req
    assert _vals(tree)["service"]["port"] == 8081
    assert _vals(tree)["service"]["targetPort"] == 8000  # backend leg untouched


# --- suite-level invariants ----------------------------------------------
def test_novelty_composition_is_three_a_three_b_two_compound():
    got = {"A": [], "B": [], "COMPOUND": []}
    for vid in VIDS:
        head = (V2 / vid / "scenario.yaml").read_text(encoding="utf-8")
        m = re.search(r"# Novelty class:\s*(A|B|COMPOUND)\b", head)
        assert m, f"{vid}: no declared novelty class"
        got[m.group(1)].append(vid)
    assert got["A"] == TYPE_A and got["B"] == TYPE_B and got["COMPOUND"] == COMPOUND


@pytest.mark.parametrize("vid", VIDS)
def test_no_conflict_markers_outside_the_sanctioned_fixture(vid):
    for f in sorted((V2 / vid).glob("*")):
        txt = f.read_text(encoding="utf-8")
        if vid == "vh08" and f.name in ("break.patch", "golden.patch"):
            continue  # vh08's fault fixture is intentionally marker-bearing
        assert not re.search(r"^[+-]?(<<<<<<< |>>>>>>> |=======$)", txt, re.M), f.name


def test_v2_scenarios_are_distinct_from_the_v1_held_out_set():
    v1 = REPO / "harness" / "scenarios" / "held-out"
    v1_breaks = {(v1 / h / "break.patch").read_text(encoding="utf-8")
                 for h in ("h01", "h02", "h03")}
    for vid in VIDS:
        assert (V2 / vid / "break.patch").read_text(encoding="utf-8") not in v1_breaks
