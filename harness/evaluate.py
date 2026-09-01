"""Step 7: deterministic scoring. No LLM judging.

Check *implementations* live in the ``harness.checks`` package (backbone,
security, config, observability, cicd, build). This module is the orchestrator:
it runs the backbone checks, activates the scenario-registered checks a run
asks for, re-balances weights, and computes the score. It also re-exports the
handful of helper names that other harness modules import from here, so
``harness.evaluate`` stays a stable import point.
"""

import json

import harness.checks  # noqa: F401  - import for side effect: registers every scenario check

# Back-compat re-exports (external callers import these from harness.evaluate).
from harness.checks import (  # noqa: F401
    _SCENARIO_CHECKS,
    _base_stdout_line_count,
    _burst_health,
    _conflict_marker_files,
    _free_port,
    _http_get_json,
    _http_health,
    _load,
    _log_lines,
    _logs_are_json,
    _parse_json_logs,
    _stdout_line_count,
    measure_stdout_lines,
    register_scenario_check,
)
from harness.checks.backbone import CHECK_WEIGHTS, run_backbone_checks  # noqa: F401


def evaluate(namespace: str, release: str, run_dir, meta: dict):
    """Run the backbone checks + any scenario-registered checks the run
    activates, re-balance weights, score 0-100, write checks.json.
    Returns (results, score) — identical shape to before the refactor."""
    results: list[dict] = run_backbone_checks(run_dir, namespace, release, meta)

    weight_overrides = meta.get("weight_overrides") or {}
    for cid in meta.get("scenario_checks") or []:
        if cid not in _SCENARIO_CHECKS:
            continue
        default_w, fn = _SCENARIO_CHECKS[cid]
        weight = int(weight_overrides.get(cid, default_w))
        try:
            ok, reason = fn(run_dir=run_dir, namespace=namespace, release=release, meta=meta)
        except Exception as exc:  # noqa: BLE001 - a check crash is a FAIL, not a raise
            ok, reason = False, f"check error: {exc}"
        results.append(
            {"id": cid, "weight": weight, "result": "PASS" if ok else "FAIL", "reason": reason}
        )
    for c in results:
        if c["id"] in weight_overrides and c["id"] not in _SCENARIO_CHECKS:
            c["weight"] = int(weight_overrides[c["id"]])

    non_na = [c for c in results if c["result"] != "NA"]
    denom = sum(c["weight"] for c in non_na) or 1
    num = sum(c["weight"] for c in results if c["result"] == "PASS")
    score = round(num / denom * 100)

    (run_dir / "checks.json").write_text(
        json.dumps({"checks": results, "score": score}, indent=2)
    )
    return results, score


def is_healthy(checks: list[dict], score: int) -> bool:
    return score == 100 and all(
        c["result"] != "FAIL" for c in checks if c["weight"] > 0
    )
