"""harness.patching: base-tree copy, frozen-subtree guard, tree-vs-base diff.

Patch application uses the pure-Python tests._diffapply (no system `patch`)."""

import pathlib

import pytest

from harness.patching import (
    _TREE_PATHS,
    _assert_frozen_subtrees,
    _copy_base_tree,
    tree_matches_base,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
SD = REPO / "harness" / "scenarios"



def test_copy_base_tree_populates_every_tree_path(base_tree):
    for name in _TREE_PATHS:
        src = REPO / name
        if src.exists():
            assert (base_tree / name).exists(), f"{name} not copied into the tree"


def test_copy_base_tree_is_byte_identical(base_tree):
    assert tree_matches_base(base_tree) == []


@pytest.mark.parametrize("sid", [f"scenario-{n:03d}" for n in range(1, 11)])
def test_break_plus_golden_round_trips_to_base(tmp_path, sid):
    import _diffapply
    tree = tmp_path / "tree"
    _copy_base_tree(tree)
    _diffapply.apply(tree, SD / sid / "break.patch")
    _diffapply.apply(tree, SD / sid / "golden.patch")
    assert tree_matches_base(tree) == []


def test_assert_frozen_subtrees_passes_clean(base_tree):
    _assert_frozen_subtrees(base_tree)  # no raise


def test_assert_frozen_subtrees_raises_on_tampered_scripts(base_tree):
    (base_tree / "scripts" / "ci.sh").write_text(
        (base_tree / "scripts" / "ci.sh").read_text(encoding="utf-8") + "\n# tamper\n",
        encoding="utf-8")
    with pytest.raises(SystemExit, match="ci.sh"):
        _assert_frozen_subtrees(base_tree)


def test_assert_frozen_subtrees_ignores_requirements_markers(base_tree):
    """scenario-010's intentional conflict markers live in requirements.txt,
    which the guard never inspects."""
    (base_tree / "requirements.txt").write_text(
        "<<<<<<< HEAD\nfastapi\n=======\nfastapi>=1\n>>>>>>> x\n", encoding="utf-8")
    _assert_frozen_subtrees(base_tree)  # no raise


def test_tree_matches_base_reports_a_single_diff(base_tree):
    (base_tree / "charts" / "app" / "values.yaml").write_text("changed\n", encoding="utf-8")
    diffs = tree_matches_base(base_tree)
    assert diffs == ["charts/app/values.yaml"]


def test_scenario_tree_tests_dir_is_only_app_tests(base_tree):
    """The scenario tree must carry only the app's own tests — never the
    harness/agents meta suite (which imports `harness`, absent in the tree)."""
    names = sorted(p.name for p in (base_tree / "tests").iterdir())
    assert names == ["test_health.py", "test_logging.py", "test_root_tier.py"]
