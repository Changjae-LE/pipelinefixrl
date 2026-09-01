"""Fixtures for the fast agent tests. No Docker / kind / K8s / network / `patch`."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # tests/ on path

REPO = pathlib.Path(__file__).resolve().parents[2]
SD = REPO / "harness" / "scenarios"
SIDS = [f"scenario-{n:03d}" for n in range(1, 11)]


@pytest.fixture
def broken_tree(tmp_path):
    """Factory: broken_tree(sid) -> a run-dir Path whose `tree/` is
    base + break.patch (applied with the pure-Python differ)."""
    import _diffapply
    from harness.patching import _copy_base_tree

    def _make(sid):
        d = tmp_path / sid
        d.mkdir(exist_ok=True)
        tree = d / "tree"
        _copy_base_tree(tree)
        _diffapply.apply(tree, SD / sid / "break.patch")
        return d
    return _make
