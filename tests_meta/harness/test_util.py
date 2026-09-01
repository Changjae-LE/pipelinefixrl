"""harness.checks._util: artifact IO, log parsing, conflict-marker scan."""

from harness.checks._util import (
    _conflict_marker_files,
    _load,
    _log_lines,
    _parse_json_logs,
)
from harness.checks.observability import _logs_are_json

JSON_LINE = '{"ts":"t","level":"INFO","msg":"m"}'
ACCESS_LINE = '{"ts":"t","level":"INFO","msg":"a","method":"GET","path":"/health","status":200}'


def test_load_missing_returns_empty(tmp_path):
    assert _load(tmp_path, "nope.json") == {}


def test_load_bad_json_returns_empty(tmp_path):
    (tmp_path / "x.json").write_text("{not json", encoding="utf-8")
    assert _load(tmp_path, "x.json") == {}


def test_load_valid_json(tmp_path):
    (tmp_path / "x.json").write_text('{"a": 1}', encoding="utf-8")
    assert _load(tmp_path, "x.json") == {"a": 1}


def test_log_lines_skips_blank_and_previous(run_dir):
    d = run_dir(logs={"pod.app.log": f"{JSON_LINE}\n\n{ACCESS_LINE}\n",
                      "pod.app.previous.log": "OLD\n"})
    assert _log_lines(d) == [JSON_LINE, ACCESS_LINE]


def test_parse_json_logs_counts():
    lines = [JSON_LINE, "plain text", ACCESS_LINE, "[]"]
    objs, total = _parse_json_logs(lines)
    assert total == 4 and len(objs) == 2  # "[]" is a list, not an object


def test_parse_json_logs_empty():
    assert _parse_json_logs([]) == ([], 0)


def test_logs_are_json_ratio(run_dir):
    # 0 lines -> False
    assert _logs_are_json(run_dir(logs={"a.log": ""})) is False
    # 100% json -> True
    assert _logs_are_json(run_dir(logs={"a.log": (JSON_LINE + "\n") * 5})) is True
    # 24/25 = 0.96 -> True
    assert _logs_are_json(run_dir(logs={"a.log": (JSON_LINE + "\n") * 24 + "plain\n"})) is True
    # 18/20 = 0.90 -> False
    assert _logs_are_json(run_dir(logs={"a.log": (JSON_LINE + "\n") * 18 + "p\np\n"})) is False


def test_conflict_marker_files_clean(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    assert _conflict_marker_files(tmp_path) == []


def test_conflict_marker_files_detects(tmp_path):
    (tmp_path / "req.txt").write_text(
        "fastapi\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> b\n", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("clean\n", encoding="utf-8")
    assert _conflict_marker_files(tmp_path) == ["req.txt"]


def test_conflict_marker_files_skips_pycache_and_binary(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.txt").write_text("<<<<<<< HEAD\n", encoding="utf-8")
    (tmp_path / "img.bin").write_bytes(b"\x00\xff<<<<<<< HEAD\x00")
    assert _conflict_marker_files(tmp_path) == []


def test_conflict_marker_equals_line_must_be_exact(tmp_path):
    (tmp_path / "a.txt").write_text("======= not a marker (has text)\n", encoding="utf-8")
    assert _conflict_marker_files(tmp_path) == []
