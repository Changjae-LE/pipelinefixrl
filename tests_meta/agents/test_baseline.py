"""harness.agents.fix_agent._baseline_patch — the offline heuristic's coverage.

Baseline solves 001/002/003/007/008/009/010; 004/005/006 are the documented
capability boundary (no_change)."""

import shutil

import pytest

from harness.agents.fix_agent import _baseline_patch

COVERED = {"scenario-001", "scenario-002", "scenario-003",
           "scenario-007", "scenario-008", "scenario-009", "scenario-010"}
BOUNDARY = {"scenario-004", "scenario-005", "scenario-006"}

pytestmark = pytest.mark.skipif(
    shutil.which("diff") is None,
    reason="_baseline_patch generates the diff with the system `diff`",
)


@pytest.mark.parametrize("sid", sorted(COVERED))
def test_baseline_produces_a_heuristic_fix(tmp_path, sid):
    patch, mode = _baseline_patch(sid, tmp_path)
    assert mode == "heuristic"
    assert patch is not None and patch.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("sid", sorted(BOUNDARY))
def test_baseline_no_change_for_boundary_scenarios(tmp_path, sid):
    patch, mode = _baseline_patch(sid, tmp_path)
    assert patch is None and mode == "no_change"


def test_baseline_s003_bumps_both_memory_lines(tmp_path):
    import pathlib

    import _diffapply
    from harness.patching import _copy_base_tree
    patch, _ = _baseline_patch("scenario-003", tmp_path)
    tree = tmp_path / "verify"
    _copy_base_tree(tree)
    sd = pathlib.Path(__file__).resolve().parents[2] / "harness" / "scenarios" / "scenario-003"
    _diffapply.apply(tree, sd / "break.patch")   # the state the baseline patch stacks on
    _diffapply.apply(tree, patch)
    txt = (tree / "charts/app/values.yaml").read_text(encoding="utf-8")
    assert "memory: 64Mi" in txt and "memory: 128Mi" in txt and "memory: 16Mi" not in txt
