"""Generalization runner plumbing (offline): golden-access guard, first-shot
row/matrix/artifact schema, --only selection, fallback-disabled invocation,
and the official ordering (no golden run inside the first-shot path)."""

import builtins
import inspect
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import harness.generalization as gen
from harness.agents import fix_agent


def _fake_prov(mode="derived", first=100, final=100, rounds=1, fallback=False,
               score=100):
    return {"scenario": "h01", "tier": "advanced", "repair_mode": mode,
            "derived_attempted": rounds > 0, "derived_validation_passed": mode == "derived",
            "fallback_used": fallback, "final_score": score,
            "files_modified": ["charts/app/values.yaml"], "rationale": "r",
            "allow_golden_fallback": False, "derived_rounds_attempted": rounds,
            "derived_rounds": [{"round": i + 1} for i in range(rounds)],
            "first_derived_score": first, "final_derived_score": final}


# --- golden-access guard --------------------------------------------------
def test_guard_blocks_every_reference_answer_path_read(tmp_path):
    # NB: the test's own name must not contain the guarded token — pytest bakes
    # the test name into tmp_path, and the guard blocks by path substring.
    safe = tmp_path / "notes.txt"
    safe.write_text("ok", encoding="utf-8")
    poisoned = tmp_path / "golden.patch"
    poisoned.write_text("secret", encoding="utf-8")
    with gen.golden_access_guard():
        assert safe.read_text(encoding="utf-8") == "ok"       # normal reads pass
        with pytest.raises(RuntimeError, match="GOLDEN ACCESS BLOCKED"):
            poisoned.read_text(encoding="utf-8")
        with pytest.raises(RuntimeError, match="GOLDEN ACCESS BLOCKED"):
            poisoned.read_bytes()
        with pytest.raises(RuntimeError, match="GOLDEN ACCESS BLOCKED"):
            open(poisoned, encoding="utf-8")
    # guard is fully lifted afterwards
    assert poisoned.read_text(encoding="utf-8") == "secret"


def test_guard_restores_on_exception(tmp_path):
    real_open = builtins.open
    with pytest.raises(ValueError):
        with gen.golden_access_guard():
            raise ValueError("boom")
    assert builtins.open is real_open


# --- selection ------------------------------------------------------------
def test_select_defaults_to_all_three():
    assert gen._select(None) == ["h01", "h02", "h03"]


def test_select_only_subset_and_rejects_unknown():
    assert gen._select("h01,h03") == ["h01", "h03"]
    with pytest.raises(ValueError, match="unknown held-out id"):
        gen._select("h01,h99")


# --- row / artifact schema ------------------------------------------------
def test_row_schema_success_and_failure():
    ok = gen.row_from_provenance("h01", 65, _fake_prov())
    assert ok["derived_success"] is True and ok["novelty"] == "A"
    fail = gen.row_from_provenance(
        "h03", 65, _fake_prov(mode="failed", first=65, final=65, rounds=3, score=65))
    assert fail["derived_success"] is False and fail["novelty"] == "B"
    for r in (ok, fail):
        assert {"scenario", "novelty", "broken", "first_derived_score",
                "final_derived_score", "derived_rounds_attempted", "derived_rounds",
                "repair_mode", "fallback_used", "final_score", "files_modified",
                "rationale", "derived_success"} <= set(r)


def test_fallback_score_never_counts_as_generalization_success():
    r = gen.row_from_provenance(
        "h02", 10, _fake_prov(mode="golden_fallback", fallback=True, score=100))
    assert r["derived_success"] is False


def test_artifact_schema_and_aggregate(tmp_path):
    rows = [gen.row_from_provenance("h01", 65, _fake_prov()),
            gen.row_from_provenance("h02", 10, _fake_prov()),
            gen.row_from_provenance(
                "h03", 65, _fake_prov(mode="failed", final=65, score=65, rounds=3))]
    out = gen.write_first_shot_artifact(rows, out_path=tmp_path / "first-shot.json")
    import json
    art = json.loads(out.read_text(encoding="utf-8"))
    assert art["agent_freeze_commit"] == gen.AGENT_FREEZE_COMMIT
    assert art["generalization_success"] == "2/3 derived"
    assert art["generalization_rate"] == pytest.approx(2 / 3)
    for s in art["scenarios"]:
        assert set(s["scenario_hashes"]) == {"scenario.yaml", "break.patch",
                                             "golden.patch"}
        assert len(s["scenario_hashes"]["break.patch"]) == 64


def test_matrix_print_smoke(capsys):
    gen.print_matrix([gen.row_from_provenance("h01", 65, _fake_prov())])
    out = capsys.readouterr().out
    assert "held-out generalization: 1 / 1 derived" in out
    assert "separate" in out  # never merged into the 001..010 number


# --- runner ordering + fallback-disabled invocation -----------------------
def test_run_first_shot_uses_frozen_agent_with_fallback_disabled(tmp_path, monkeypatch):
    calls = []

    def fake_run_scenario(sid, variant, enforce=True, agent_patch=None):
        calls.append(("scenario", sid, variant, enforce))
        return f"{sid}-{variant}-x", 65, {}

    def fake_agent_run(sid, tier, enforce=False, allow_golden_fallback=True):
        # the guard must be armed while the frozen agent runs
        with pytest.raises(RuntimeError, match="GOLDEN ACCESS BLOCKED"):
            (tmp_path / "golden.probe").write_text("x", encoding="utf-8")
        calls.append(("agent", sid, tier, allow_golden_fallback))
        return f"{sid}-advanced-x", 65, {}, _fake_prov(mode="failed", final=65,
                                                       score=65)

    monkeypatch.setattr(gen.scenmod, "run_scenario", fake_run_scenario)
    monkeypatch.setattr(gen.scenmod, "cleanup_scenario_ns", lambda *a, **k: "ns")
    monkeypatch.setattr(fix_agent, "run", fake_agent_run)
    monkeypatch.setattr(gen, "AGENTS_STATE", tmp_path / "agents")

    rows = gen.run_first_shot(only="h01")
    assert calls == [("scenario", "h01", "broken", True),
                     ("agent", "h01", "advanced", False)]
    assert rows[0]["derived_success"] is False
    # no golden variant ran anywhere in the first-shot path
    assert not any(c[0] == "scenario" and c[2] == "golden" for c in calls)
    assert (tmp_path / "agents" / "generalization.json").is_file()


def test_first_shot_source_never_runs_golden_variant():
    src = inspect.getsource(gen.run_first_shot)
    assert '"golden"' not in src
    assert "allow_golden_fallback=False" in src


def test_golden_validate_is_a_separate_post_archive_step():
    src = inspect.getsource(gen.golden_validate)
    assert '"golden"' in src and "compose_check" in src
