"""harness.agents.fix_agent._edits_to_patch — structural + round-trip.

Structure/round-trip assertions are pure Python. The one test that shells to
`diff` (the production generator) is skipped when `diff` is absent.
All fixture files are written LF-only to match the byte-preserving trees the
production path works with."""

import pathlib
import shutil

import pytest

from harness.agents.fix_agent import _edits_to_patch

pytestmark = pytest.mark.skipif(shutil.which("diff") is None,
                                reason="_edits_to_patch generates diffs with the system `diff`")


def _w(p: pathlib.Path, text: str):
    p.write_bytes(text.encode("utf-8"))  # no CRLF translation


def test_single_file_patch_applies_back(tmp_path):
    import _diffapply
    broken = tmp_path / "tree"
    broken.mkdir()
    _w(broken / "f.txt", "one\ntwo\nthree\n")
    new = b"one\nTWO\nthree\n"
    p = _edits_to_patch(broken, [("f.txt", new)], tmp_path / "out.patch")
    txt = pathlib.Path(p).read_text(encoding="utf-8")
    assert txt.startswith("--- a/f.txt") and "+++ b/f.txt" in txt and "@@ " in txt
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _w(fresh / "f.txt", "one\ntwo\nthree\n")
    _diffapply.apply(fresh, p)
    assert (fresh / "f.txt").read_bytes() == new


def test_noop_edit_produces_empty_patch(tmp_path):
    broken = tmp_path / "tree"
    broken.mkdir()
    _w(broken / "f.txt", "same\n")
    p = _edits_to_patch(broken, [("f.txt", b"same\n")], tmp_path / "out.patch")
    assert pathlib.Path(p).read_text(encoding="utf-8") == ""


def test_multi_file_patch(tmp_path):
    import _diffapply
    broken = tmp_path / "tree"
    (broken / "d").mkdir(parents=True)
    _w(broken / "a.txt", "a1\n")
    _w(broken / "d" / "b.txt", "b1\n")
    p = _edits_to_patch(
        broken, [("a.txt", b"a2\n"), ("d/b.txt", b"b2\n")], tmp_path / "out.patch")
    txt = pathlib.Path(p).read_text(encoding="utf-8")
    assert txt.count("--- a/") == 2
    fresh = tmp_path / "fresh"
    (fresh / "d").mkdir(parents=True)
    _w(fresh / "a.txt", "a1\n")
    _w(fresh / "d" / "b.txt", "b1\n")
    _diffapply.apply(fresh, p)
    assert (fresh / "a.txt").read_bytes() == b"a2\n"
    assert (fresh / "d" / "b.txt").read_bytes() == b"b2\n"
