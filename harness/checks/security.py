"""Pod-security posture checks (owning scenario: scenario-006). Read the applied
container securityContext from pods.json."""

from harness.checks import register_scenario_check
from harness.checks._util import _load


def _applied_container_sc(run_dir):
    """The first app container's applied securityContext from pods.json."""
    pods = _load(run_dir, "pods.json")
    items = pods.get("items", []) if isinstance(pods, dict) else []
    if not items:
        return {}
    containers = (items[0].get("spec") or {}).get("containers") or [{}]
    return containers[0].get("securityContext") or {}


@register_scenario_check("runs_as_nonroot", 15)
def _runs_as_nonroot(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-006. Applied container must declare
    runAsNonRoot true and a numeric runAsUser >= 1000."""
    sc = _applied_container_sc(run_dir)
    ru = sc.get("runAsUser")
    ok = sc.get("runAsNonRoot") is True and isinstance(ru, int) and not isinstance(ru, bool) and ru >= 1000
    return ok, f"runAsNonRoot={sc.get('runAsNonRoot')} runAsUser={ru}"


@register_scenario_check("readonly_rootfs", 10)
def _readonly_rootfs(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-006."""
    sc = _applied_container_sc(run_dir)
    return sc.get("readOnlyRootFilesystem") is True, f"readOnlyRootFilesystem={sc.get('readOnlyRootFilesystem')}"


@register_scenario_check("no_priv_escalation", 10)
def _no_priv_escalation(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-006."""
    sc = _applied_container_sc(run_dir)
    return sc.get("allowPrivilegeEscalation") is False, f"allowPrivilegeEscalation={sc.get('allowPrivilegeEscalation')}"


@register_scenario_check("caps_dropped", 10)
def _caps_dropped(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-006. capabilities.drop must contain ALL and
    capabilities.add must be empty/absent."""
    caps = _applied_container_sc(run_dir).get("capabilities") or {}
    drop = caps.get("drop") or []
    add = caps.get("add") or []
    return ("ALL" in drop and not add), f"drop={drop} add={add}"
