"""harness.agents.fix_agent._resolve_conflict_keep_head."""

from harness.agents.fix_agent import _resolve_conflict_keep_head


def test_single_hunk_keeps_head():
    src = "a\n<<<<<<< HEAD\nkeep-me\n=======\ndrop-me\n>>>>>>> other\nz\n"
    assert _resolve_conflict_keep_head(src) == "a\nkeep-me\nz\n"


def test_multi_hunk():
    src = (
        "top\n"
        "<<<<<<< HEAD\nh1\n=======\nt1\n>>>>>>> b\n"
        "mid\n"
        "<<<<<<< HEAD\nh2a\nh2b\n=======\nt2\n>>>>>>> b\n"
        "bot\n"
    )
    assert _resolve_conflict_keep_head(src) == "top\nh1\nmid\nh2a\nh2b\nbot\n"


def test_no_markers_passthrough():
    src = "line one\nline two\n"
    assert _resolve_conflict_keep_head(src) == src


def test_drops_incoming_side_entirely():
    src = "<<<<<<< HEAD\n=======\nonly-incoming\n>>>>>>> b\n"
    assert _resolve_conflict_keep_head(src) == ""


def test_preserves_surrounding_text():
    src = "import a\n<<<<<<< HEAD\nX = 1\n=======\nX = 2\n>>>>>>> feat\nimport b\n"
    assert _resolve_conflict_keep_head(src) == "import a\nX = 1\nimport b\n"
