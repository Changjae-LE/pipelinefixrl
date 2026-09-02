"""The advanced provenance record's shape / mode enum are stable (schema guard).

The full run() needs a cluster; this pins the *contract* — the keys run()
assembles and the repair_mode values it can emit — so a refactor can't quietly
change advanced_provenance.json. The Improvement-2 iterative extension is
additive: every pre-Improvement-2 key must keep its exact name."""

import inspect

import harness.agents.fix_agent as fa

# frozen pre-Improvement-2 contract — these names may never change
LEGACY_KEYS = {
    "scenario", "tier", "repair_mode", "derived_attempted",
    "derived_validation_passed", "fallback_used", "final_score",
    "files_modified", "rationale",
}
# additive iterative extension
EXTENSION_KEYS = {
    "allow_golden_fallback", "derived_rounds_attempted", "derived_rounds",
    "first_derived_score", "final_derived_score",
}
EXPECTED_MODES = {"derived", "golden_fallback", "no_change", "failed"}


def _prov_literal():
    src = inspect.getsource(fa.run)
    start = src.index('prov = {"scenario": scenario_id, "tier": "advanced"')
    return src[start:start + 1200]


def test_run_initialises_provenance_with_the_legacy_keys():
    frag = _prov_literal()
    for k in LEGACY_KEYS:
        assert f'"{k}"' in frag, f"legacy provenance key {k!r} missing from run()"


def test_run_initialises_provenance_with_the_iterative_extension_keys():
    frag = _prov_literal()
    for k in EXTENSION_KEYS:
        assert f'"{k}"' in frag, f"extension provenance key {k!r} missing from run()"


def test_repair_mode_literals_are_the_documented_enum():
    src = inspect.getsource(fa.run)
    used = set()
    for token in ('"derived"', '"golden_fallback"', '"no_change"', '"failed"'):
        if token in src:
            used.add(token.strip('"'))
    assert used <= EXPECTED_MODES
    assert {"derived", "golden_fallback"} <= used  # both real outcomes are reachable


def test_round_records_carry_the_documented_fields():
    src = inspect.getsource(fa.run)
    start = src.index('prov["derived_rounds"].append({')
    frag = src[start:start + 700]
    for k in ("round", "evidence_sources", "findings", "edit_conflicts",
              "rationale", "files_modified", "validation_score", "validation_passed"):
        assert f'"{k}"' in frag, f"round-record field {k!r} missing"


def test_emit_advanced_writes_advanced_provenance_json(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(fa, "RUNS_DIR", tmp_path)
    (tmp_path / "rid").mkdir()
    prov = {"scenario": "scenario-001", "repair_mode": "derived", "final_score": 100,
            "derived_attempted": True, "derived_validation_passed": True,
            "fallback_used": False, "derived_rounds_attempted": 1}
    fa._emit_advanced("rid", prov)
    written = json.loads((tmp_path / "rid" / "advanced_provenance.json").read_text())
    assert set(written) >= {"scenario", "repair_mode", "final_score"}
