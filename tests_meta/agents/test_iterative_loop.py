"""The iterative derive → apply → validate → observe → refine loop (offline).

run()'s cluster interactions are stubbed at the documented seams
(_validate_candidate, _edits_to_patch, _latest_broken_run, compose) so the loop
logic itself — round bookkeeping, refinement, the 3-round cap, fallback
ordering, the fallback-disabled generalization mode, per-round provenance —
is exercised deterministically with no Docker/kind/K8s/network/diff/patch."""

import builtins
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import harness.agents.fix_agent as fa
import harness.agents.primitives as prims

SID = "scenario-001"
ROUND_FIELDS = {"round", "evidence_sources", "findings", "edit_conflicts",
                "rationale", "files_modified", "validation_score", "validation_passed"}


class Harness:
    """Scripted seams: compose writes round-specific bytes; validation returns
    scripted scores; everything is recorded for assertions."""

    def __init__(self, tmp_path, monkeypatch, scores, edits_per_round=None):
        self.scores = list(scores)
        self.edits_per_round = edits_per_round  # None => a new edit every round
        self.validations = []
        self.round_no = 0

        runs = tmp_path / "runs"
        agents = tmp_path / "agents"
        broken = runs / f"{SID}-broken-x"
        (broken / "tree").mkdir(parents=True)
        (broken / "tree" / "f.txt").write_text("orig\n", encoding="utf-8")
        (broken / "events.txt").write_text("Warning SomethingBroke", encoding="utf-8")
        self.runs, self.broken = runs, broken

        monkeypatch.setattr(fa, "RUNS_DIR", runs)
        monkeypatch.setattr(fa, "AGENTS_STATE", agents)
        monkeypatch.setattr(fa, "_latest_broken_run", lambda sid: broken)
        monkeypatch.setattr(fa, "_safe_cleanup_advanced", lambda sid: None)
        monkeypatch.setattr(fa, "_edits_to_patch", self._fake_patch)
        monkeypatch.setattr(prims, "compose", self._fake_compose)
        monkeypatch.setattr(fa, "_validate_candidate", self._fake_validate)

    def _fake_patch(self, broken_tree, edits, out_path):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("--- fake patch ---\n", encoding="utf-8")
        return out_path

    def _fake_compose(self, tree, ev, primitives=None):
        self.round_no += 1
        res = prims.ComposeResult(applied=[], duplicates=[], conflicts=[],
                                  rationales=[], changed=set())
        n_edits = (self.edits_per_round[self.round_no - 1]
                   if self.edits_per_round else 1)
        if n_edits:
            (tree / "f.txt").write_text(f"fix round {self.round_no}\n", encoding="utf-8")
            res.changed.add("f.txt")
            res.applied.append({"primitive": "prim_x", "diagnosis": f"diag_r{self.round_no}",
                                "file": "f.txt", "rationale": "scripted"})
            res.rationales.append(f"prim_x.diag_r{self.round_no}: scripted")
        return res

    def _fake_validate(self, sid, patch):
        score = self.scores[len(self.validations)]
        self.validations.append(patch)
        rid = f"{SID}-advanced-r{len(self.validations)}"
        (self.runs / rid).mkdir(parents=True, exist_ok=True)
        verdict = {"anticheat_violations": [],
                   "matches_expectation": score == 100}
        return rid, score, verdict


def test_first_round_success(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, scores=[100])
    rid, score, verdict, prov = fa.run(SID, "advanced")
    assert score == 100
    assert prov["repair_mode"] == "derived" and prov["fallback_used"] is False
    assert prov["derived_rounds_attempted"] == 1
    assert prov["first_derived_score"] == prov["final_derived_score"] == 100
    assert len(prov["derived_rounds"]) == 1
    assert len(h.validations) == 1


def test_failed_first_round_then_successful_refinement(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, scores=[55, 100])
    rid, score, verdict, prov = fa.run(SID, "advanced")
    assert score == 100
    assert prov["repair_mode"] == "derived" and prov["fallback_used"] is False
    assert prov["derived_rounds_attempted"] == 2
    # the iterative history must not present this as an immediate solve
    assert prov["first_derived_score"] == 55
    assert prov["final_derived_score"] == 100
    assert [r["validation_passed"] for r in prov["derived_rounds"]] == [False, True]
    assert len(h.validations) == 2


def test_no_round_is_silently_discarded(tmp_path, monkeypatch):
    Harness(tmp_path, monkeypatch, scores=[55, 60, 100])
    _, _, _, prov = fa.run(SID, "advanced")
    assert len(prov["derived_rounds"]) == prov["derived_rounds_attempted"] == 3
    for rec in prov["derived_rounds"]:
        assert ROUND_FIELDS <= set(rec)
    assert [r["round"] for r in prov["derived_rounds"]] == [1, 2, 3]
    assert [r["validation_score"] for r in prov["derived_rounds"]] == [55, 60, 100]


def test_fallback_only_after_all_rounds_exhausted(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, scores=[55, 60, 70])
    fallback_calls = []

    def fake_run_scenario(sid, variant, enforce=False, agent_patch=None):
        fallback_calls.append(str(agent_patch))
        rid = f"{SID}-advanced-fb"
        (h.runs / rid).mkdir(parents=True, exist_ok=True)
        return rid, 100, {"anticheat_violations": [], "matches_expectation": True}
    monkeypatch.setattr(fa.scenmod, "run_scenario", fake_run_scenario)

    rid, score, verdict, prov = fa.run(SID, "advanced")
    assert len(h.validations) == fa.MAX_DERIVED_ROUNDS == 3   # cap respected
    assert len(fallback_calls) == 1                            # fallback ran once, last
    assert fallback_calls[0].endswith("golden.patch")
    assert prov["repair_mode"] == "golden_fallback" and prov["fallback_used"] is True
    assert prov["derived_rounds_attempted"] == 3
    assert prov["files_modified"][-1] == "golden.patch (fallback)"
    assert prov["final_derived_score"] == 70 and prov["final_score"] == 100


def test_fallback_disabled_never_touches_golden(tmp_path, monkeypatch):
    Harness(tmp_path, monkeypatch, scores=[55, 60, 70])
    monkeypatch.setattr(
        fa.scenmod, "run_scenario",
        lambda *a, **k: pytest.fail("run_scenario must not be called with fallback disabled"))
    # poison every golden path read for the whole run
    real_rt, real_rb, real_open = pathlib.Path.read_text, pathlib.Path.read_bytes, builtins.open

    def guard(fn):
        def _w(target, *a, **k):
            assert "golden" not in str(target).lower(), f"golden access: {target}"
            return fn(target, *a, **k)
        return _w
    monkeypatch.setattr(pathlib.Path, "read_text", guard(real_rt))
    monkeypatch.setattr(pathlib.Path, "read_bytes", guard(real_rb))
    monkeypatch.setattr(builtins, "open", guard(real_open))

    rid, score, verdict, prov = fa.run(SID, "advanced", allow_golden_fallback=False)
    assert prov["repair_mode"] == "failed" and prov["fallback_used"] is False
    assert prov["allow_golden_fallback"] is False
    assert prov["derived_rounds_attempted"] == 3
    assert score == 70 and prov["final_score"] == 70   # honest failing score


def test_fallback_disabled_no_change_when_nothing_derivable(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, scores=[10], edits_per_round=[0, 0, 0])
    rid, score, verdict, prov = fa.run(SID, "advanced", allow_golden_fallback=False)
    assert prov["repair_mode"] == "no_change" and prov["derived_attempted"] is False
    assert prov["derived_rounds"] == []
    # the unmodified broken tree was scored once (patch None), nothing else ran
    assert h.validations == [None]
    assert score == 10


def test_refinement_stops_when_no_new_edits(tmp_path, monkeypatch):
    # round 1 fails; round 2's compose finds nothing new -> loop ends, fallback runs
    h = Harness(tmp_path, monkeypatch, scores=[55], edits_per_round=[1, 0, 0])
    calls = []

    def fake_run_scenario(sid, variant, enforce=False, agent_patch=None):
        calls.append(str(agent_patch))
        rid = f"{SID}-advanced-fb"
        (h.runs / rid).mkdir(parents=True, exist_ok=True)
        return rid, 100, {"anticheat_violations": [], "matches_expectation": True}
    monkeypatch.setattr(fa.scenmod, "run_scenario", fake_run_scenario)

    _, _, _, prov = fa.run(SID, "advanced")
    assert prov["derived_rounds_attempted"] == 1        # only one candidate existed
    assert len(h.validations) == 1
    assert prov["repair_mode"] == "golden_fallback"
