"""scenario-010's intentional conflict markers must be allowed as the fault
fixture in the ephemeral broken tree — but golden must still resolve them."""

from harness.anticheat import run_scenario_anticheat, universal_anticheat
from harness.checks.build import _git_tree_resolved
from harness.patching import _assert_frozen_subtrees

MARKED_REQS = (
    "<<<<<<< HEAD\nfastapi>=0.110,<1.0\nuvicorn[standard]>=0.27,<1.0\n"
    "=======\nfastapi>=0.115,<1.0\nuvicorn[standard]>=0.32,<1.0\n>>>>>>> origin/deps-bump\n"
)


def test_universal_anticheat_ignores_requirements_markers(base_tree):
    (base_tree / "requirements.txt").write_text(MARKED_REQS, encoding="utf-8")
    assert universal_anticheat(base_tree) == []


def test_frozen_subtree_guard_ignores_requirements_markers(base_tree):
    (base_tree / "requirements.txt").write_text(MARKED_REQS, encoding="utf-8")
    _assert_frozen_subtrees(base_tree)  # no raise


def test_git_tree_resolved_fails_on_the_broken_tree(tmp_path):
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "requirements.txt").write_text(MARKED_REQS, encoding="utf-8")
    ok, why = _git_tree_resolved(run_dir=tmp_path, namespace="n", release="app", meta={})
    assert not ok and "requirements.txt" in why


def test_merge_resolved_cleanly_flags_the_broken_tree(base_tree):
    (base_tree / "requirements.txt").write_text(MARKED_REQS, encoding="utf-8")
    cfg = {"evaluation": {"anticheat": {"merge_resolved_cleanly": True}}}
    v = run_scenario_anticheat(cfg, base_tree, loaded_tags=[])
    assert any("conflict-marker lines" in x for x in v)
