"""ConfigMap-reference integrity check (owning scenario: scenario-007)."""

import yaml

from harness.checks import register_scenario_check
from harness.checks._util import _http_get_json, _load


@register_scenario_check("config_applied", 15)
def _config_applied(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-007. The rendered Deployment must still source a
    container env var from a ConfigMap (configMapKeyRef), and GET / through the
    Service must return that config-sourced `tier` equal to the value the variant
    tree declares for the ConfigMap (config.tier in its values.yaml)."""
    dep = _load(run_dir, "rollout.json")
    containers = (((dep.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    ref_ok = any(
        (e.get("valueFrom") or {}).get("configMapKeyRef")
        for c in containers
        for e in (c.get("env") or [])
    )
    if not ref_ok:
        return False, "rendered Deployment has no env var sourced from a configMapKeyRef"

    try:
        vals = yaml.safe_load(
            (run_dir / "tree" / "charts" / "app" / "values.yaml").read_text(encoding="utf-8")
        )
    except OSError as e:
        return False, f"cannot read variant values.yaml: {e}"
    want = str(((vals or {}).get("config") or {}).get("tier", ""))
    if not want:
        return False, "variant values.yaml has empty config.tier"

    ok, parsed, detail = _http_get_json(namespace, release, "/")
    if not ok:
        return False, detail
    got = parsed.get("tier") if isinstance(parsed, dict) else None
    return got == want, f"GET / tier={got!r} want={want!r} ({detail})"
