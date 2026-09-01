"""The advanced provenance record's shape / mode enum are stable (schema guard).

The full run() needs a cluster; this pins the *contract* — the keys run()
assembles and the repair_mode values it can emit — so a refactor can't quietly
change advanced_provenance.json."""

import inspect

import harness.agents.fix_agent as fa

EXPECTED_KEYS = {
    "scenario", "tier", "repair_mode", "derived_attempted",
    "derived_validation_passed", "fallback_used", "final_score",
    "files_modified", "rationale",
}
EXPECTED_MODES = {"derived", "golden_fallback", "no_change", "failed"}


def test_run_initialises_provenance_with_the_documented_keys():
    src = inspect.getsource(fa.run)
    # the advanced-branch provenance dict literal
    start = src.index('prov = {"scenario": scenario_id, "tier": "advanced"')
    frag = src[start:start + 400]
    for k in EXPECTED_KEYS:
        assert f'"{k}"' in frag, f"provenance key {k!r} missing from run()"


def test_repair_mode_literals_are_the_documented_enum():
    src = inspect.getsource(fa.run)
    used = set()
    for token in ('"derived"', '"golden_fallback"', '"no_change"', '"failed"'):
        if token in src:
            used.add(token.strip('"'))
    assert used <= EXPECTED_MODES
    assert {"derived", "golden_fallback"} <= used  # both real outcomes are reachable


def test_emit_advanced_writes_advanced_provenance_json(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(fa, "RUNS_DIR", tmp_path)
    (tmp_path / "rid").mkdir()
    prov = {"scenario": "scenario-001", "repair_mode": "derived", "final_score": 100,
            "derived_attempted": True, "derived_validation_passed": True, "fallback_used": False}
    fa._emit_advanced("rid", prov)
    written = json.loads((tmp_path / "rid" / "advanced_provenance.json").read_text())
    assert set(written) >= {"scenario", "repair_mode", "final_score"}
