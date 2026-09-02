"""The scenario-check registry: exact ids and default weights."""

import harness.evaluate as e
from harness.checks import _SCENARIO_CHECKS, register_scenario_check

EXPECTED = {
    "image_pull_ok": 15,
    "no_oomkill": 10,
    "service_selects_pods": 15,
    "runs_as_nonroot": 15,
    "readonly_rootfs": 10,
    "no_priv_escalation": 10,
    "caps_dropped": 10,
    "config_applied": 15,
    "structured_logs_ok": 35,
    "ci_gate_pass": 30,
    "image_build_ok": 20,
    "git_tree_resolved": 10,
    # Improvement 2 benchmark-side addition (held-out h01/h03 port relationships)
    "service_ports_wired": 15,
}


def test_exact_registered_check_set():
    assert set(_SCENARIO_CHECKS) == set(EXPECTED)


def test_default_weights_unchanged():
    assert {cid: w for cid, (w, _fn) in _SCENARIO_CHECKS.items()} == EXPECTED


def test_evaluate_reexports_same_registry_object():
    assert e._SCENARIO_CHECKS is _SCENARIO_CHECKS


def test_register_scenario_check_adds_and_overrides():
    @register_scenario_check("_unit_probe", 7)
    def _probe(**_kw):
        return True, "ok"

    try:
        assert _SCENARIO_CHECKS["_unit_probe"][0] == 7
        assert _SCENARIO_CHECKS["_unit_probe"][1] is _probe
    finally:
        _SCENARIO_CHECKS.pop("_unit_probe", None)
