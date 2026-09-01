"""Deterministic scenario-check registry.

A scenario milestone registers an extra check with ``@register_scenario_check``
in one of the domain modules below. ``evaluate()`` (harness/evaluate.py) reads
``_SCENARIO_CHECKS`` and activates a check when a run lists it in
``meta['scenario_checks']``. Weights are re-balanced via
``meta['weight_overrides']`` so every scenario still totals 100.
"""

_SCENARIO_CHECKS: dict = {}


def register_scenario_check(check_id: str, default_weight: int):
    def _deco(fn):
        _SCENARIO_CHECKS[check_id] = (default_weight, fn)
        return fn

    return _deco


# Import the domain modules so their @register_scenario_check decorators run.
from harness.checks import (  # noqa: E402,F401  (import-for-side-effects: registration)
    backbone,
    build,
    cicd,
    config,
    observability,
    security,
)

# Re-exports so harness.evaluate can stay a thin compatibility facade and so
# external callers keep importing these from a stable location.
from harness.checks._util import (  # noqa: E402,F401
    _burst_health,
    _conflict_marker_files,
    _free_port,
    _http_get_json,
    _http_health,
    _load,
    _log_lines,
    _parse_json_logs,
    measure_stdout_lines,
)
from harness.checks.backbone import CHECK_WEIGHTS, run_backbone_checks  # noqa: E402,F401
from harness.checks.observability import (  # noqa: E402,F401
    _base_stdout_line_count,
    _logs_are_json,
    _stdout_line_count,
)
