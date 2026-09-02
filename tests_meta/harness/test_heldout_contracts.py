"""Held-out scenario contracts (offline): loader resolution, definition shape,
break+golden byte-identical round-trip, and the h02 Unschedulable static
precondition. No Docker/kind/K8s/network/system patch."""

import pathlib
import re
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import _diffapply
from harness import scenario as scenmod
from harness.anticheat import _ANTICHEAT_RULES, _mem_to_bytes
from harness.patching import _copy_base_tree, tree_matches_base

REPO = pathlib.Path(__file__).resolve().parents[2]
HELD = REPO / "harness" / "scenarios" / "held-out"
HIDS = ["h01", "h02", "h03"]


# --- loader resolution ----------------------------------------------------
def test_original_scenarios_resolve_exactly_as_before():
    d = scenmod._scenario_dir("scenario-001")
    assert d == scenmod.SCENARIOS_DIR / "scenario-001" and d.is_dir()


@pytest.mark.parametrize("hid", HIDS)
def test_held_out_ids_resolve_into_the_held_out_directory(hid):
    d = scenmod._scenario_dir(hid)
    assert d == scenmod.SCENARIOS_DIR / "held-out" / hid and d.is_dir()


def test_unknown_id_still_resolves_to_the_plain_missing_path():
    d = scenmod._scenario_dir("scenario-999")
    assert d == scenmod.SCENARIOS_DIR / "scenario-999" and not d.exists()


# --- definition shape -----------------------------------------------------
@pytest.mark.parametrize("hid", HIDS)
def test_definition_is_complete_and_well_formed(hid):
    sdir = HELD / hid
    for name in ("scenario.yaml", "task.md", "break.patch", "golden.patch"):
        assert (sdir / name).is_file(), f"{hid}: missing {name}"
    cfg = yaml.safe_load((sdir / "scenario.yaml").read_text(encoding="utf-8"))
    assert cfg["id"] == hid
    assert cfg["patches"] == {"break": "break.patch", "golden": "golden.patch"}
    ev = cfg["evaluation"]
    exp = ev["expect"]
    assert exp["golden"]["score_min"] == 100 and exp["golden"]["anticheat_clean"] is True
    assert exp["broken"]["score_max"] < 100
    # fail-closed compatibility: every declared anti-cheat rule is registered
    assert set(ev.get("anticheat") or {}) <= set(_ANTICHEAT_RULES)
    # declared checks exist in the registry
    from harness.checks import _SCENARIO_CHECKS
    assert set(ev.get("checks") or []) <= set(_SCENARIO_CHECKS)


@pytest.mark.parametrize("hid", ["h01", "h03"])
def test_port_scenarios_measure_the_fault_through_the_port_check(hid):
    cfg = yaml.safe_load((HELD / hid / "scenario.yaml").read_text(encoding="utf-8"))
    exp = cfg["evaluation"]["expect"]
    assert "service_ports_wired" in cfg["evaluation"]["checks"]
    assert "service_ports_wired" in exp["broken"]["must_fail"]
    assert "endpoints_present" in exp["broken"]["must_pass"]  # backend stays healthy
    w = dict(cfg["evaluation"]["weights"])
    total = (10 + 15 + 20 + 0  # helm + endpoints + http + no_bad_events defaults
             + sum(w.values()))
    assert total == 100


def test_h02_measures_the_scheduling_fault_for_the_intended_reason():
    cfg = yaml.safe_load((HELD / "h02" / "scenario.yaml").read_text(encoding="utf-8"))
    evd = cfg["evaluation"]["expect"]["broken"]["evidence"]
    assert "FailedScheduling" in evd["events_contains"]
    assert "Insufficient memory" in evd["events_contains"]
    assert "Pending" in evd["pods_json_contains"]
    # the min_memory floor blocks the shrink-to-nothing cheat
    assert cfg["evaluation"]["anticheat"]["min_memory"] == {
        "requests": "32Mi", "limits": "64Mi"}


# --- compose round-trip (pure-Python patch mechanics) ---------------------
@pytest.mark.parametrize("hid", HIDS)
def test_break_plus_golden_round_trips_to_base(hid, tmp_path):
    tree = tmp_path / "tree"
    _copy_base_tree(tree)
    _diffapply.apply(tree, HELD / hid / "break.patch")
    assert tree_matches_base(tree) != [], f"{hid}: break.patch changed nothing"
    _diffapply.apply(tree, HELD / hid / "golden.patch")
    assert tree_matches_base(tree) == [], f"{hid}: break+golden != base"


@pytest.mark.parametrize("hid", HIDS)
def test_no_conflict_markers_in_held_out_files(hid):
    for f in sorted((HELD / hid).glob("*")):
        txt = f.read_text(encoding="utf-8")
        assert not re.search(r"^(<<<<<<< |>>>>>>> |=======$)", txt, re.M), f.name


# --- h02 static Unschedulable precondition --------------------------------
def test_h02_broken_request_is_absurdly_beyond_any_kind_node(tmp_path):
    tree = tmp_path / "tree"
    _copy_base_tree(tree)
    _diffapply.apply(tree, HELD / "h02" / "break.patch")
    v = yaml.safe_load((tree / "charts/app/values.yaml").read_text(encoding="utf-8"))
    req = _mem_to_bytes(v["resources"]["requests"]["memory"])
    # static determinism bar: >= 8Gi is beyond any realistic kind-node
    # allocatable; Phase 4B additionally records the live node's allocatable
    # memory and asserts the request exceeds it before trusting the runtime.
    assert req >= 8 * 1024**3
    assert req == 64 * 1024**3
