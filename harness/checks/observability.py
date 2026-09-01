"""Structured-logging check + log-shape helpers (owning scenario: scenario-008)."""

import json

from harness.checks import register_scenario_check
from harness.checks._util import (
    _burst_health,
    _log_lines,
    _parse_json_logs,
    measure_stdout_lines,
)
from harness.paths import RUNS_DIR


def _logs_are_json(run_dir, threshold: float = 0.95) -> bool:
    """True iff there is >= 1 stdout line and >= `threshold` of non-blank lines
    parse as JSON objects. Scenario-agnostic; feeds verdict.json `logs_are_json`."""
    lines = _log_lines(run_dir)
    if not lines:
        return False
    objs, total = _parse_json_logs(lines)
    return (len(objs) / total) >= threshold


def _stdout_line_count(run_dir) -> int:
    """Count of non-blank stdout lines in the run's collected pod logs."""
    return len(_log_lines(run_dir))


def _base_stdout_line_count():
    """The `stdout_line_count` recorded by the most recent base run
    (harness.run.run_variant), or None if it cannot be read."""
    try:
        run_id = (RUNS_DIR / "last-base").read_text(encoding="utf-8").strip()
        meta = json.loads((RUNS_DIR / run_id / "meta.json").read_text(encoding="utf-8"))
        v = meta.get("stdout_line_count")
        return int(v) if isinstance(v, int) and not isinstance(v, bool) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


@register_scenario_check("structured_logs_ok", 35)
def _structured_logs_ok(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-008.

    PASS iff: >= 95 % of non-blank stdout lines are JSON objects; every object
    carries {ts, level, msg}; >= 1 object is an HTTP access record with
    {method, path, status} and 2xx status; a live GET /health through the
    Service returns 200; and this healthy run's stdout line count (fixed
    synthetic load) is >= the most recent base healthy run's — so "fixing" the
    logs by raising logLevel to suppress the access lines is caught.

    Records its measurements on ``meta['structured_logs']`` so run_scenario can
    surface the same numbers in verdict.json (the M9 contract's logs_are_json /
    stdout line counts) rather than a second, inconsistent snapshot."""
    base = _base_stdout_line_count()
    sl = {"logs_are_json": False, "json_ratio": 0.0, "access_records": 0,
          "stdout_line_count": None, "base_stdout_line_count": base}
    meta["structured_logs"] = sl

    lines = _log_lines(run_dir)
    if not lines:
        return False, "no stdout lines captured"
    objs, total = _parse_json_logs(lines)
    ratio = len(objs) / total
    sl["json_ratio"] = round(ratio, 4)
    sl["logs_are_json"] = ratio >= 0.95
    if ratio < 0.95:
        return False, f"{len(objs)}/{total} stdout lines are valid JSON (need >=95%)"
    bad = [o for o in objs if not {"ts", "level", "msg"} <= set(o)]
    if bad:
        return False, f"{len(bad)}/{len(objs)} JSON log objects lack required keys {{ts,level,msg}}"
    access = [
        o for o in objs
        if {"method", "path", "status"} <= set(o)
        and str(o.get("status")).isdigit() and 200 <= int(o["status"]) < 300
    ]
    sl["access_records"] = len(access)
    if not access:
        return False, "no structured 2xx HTTP access record on stdout"
    if _burst_health(namespace, release, 1) < 1:
        return False, "live GET /health through the Service did not return 200"
    if base is None:
        return False, "baseline stdout line count unavailable (run make e2e-base first)"
    try:
        this = measure_stdout_lines(namespace, release, 10)
    except RuntimeError as e:
        return False, f"could not measure stdout line count: {e}"
    sl["stdout_line_count"] = this
    if this < base:
        return False, f"stdout line count {this} < base healthy {base} (access logging suppressed?)"
    return True, (
        f"{len(objs)}/{total} JSON lines; {len(access)} access rec(s); "
        f"stdout_lines this={this} >= base={base}"
    )
