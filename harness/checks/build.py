"""Image-acquisition, OOM, docker-build and git-tree checks
(owning scenarios: 002 image pull, 003 OOMKilled, 010 merge conflict)."""

from harness.checks import register_scenario_check
from harness.checks._util import _conflict_marker_files, _load
from harness.paths import VERSIONS

_IMAGE_PROBLEM_REASONS = {
    "ErrImageNeverPull",
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
}


@register_scenario_check("image_pull_ok", 15)
def _image_pull_ok(*, run_dir, namespace, release, meta):
    """PASS iff no container is stuck in an image-acquisition waiting state.
    Owning scenario: scenario-002 (produces ErrImageNeverPull)."""
    pods = _load(run_dir, "pods.json")
    items = pods.get("items", []) if isinstance(pods, dict) else []
    hits = []
    for p in items:
        st = p.get("status") or {}
        for cs in (st.get("containerStatuses") or []) + (st.get("initContainerStatuses") or []):
            reason = ((cs.get("state") or {}).get("waiting") or {}).get("reason")
            if reason in _IMAGE_PROBLEM_REASONS:
                hits.append(f"{cs.get('name')}: {reason}")
    if hits:
        return False, "image acquisition failed — " + "; ".join(hits)
    return True, "no image-acquisition waiting states"


@register_scenario_check("no_oomkill", 10)
def _no_oomkill(*, run_dir, namespace, release, meta):
    """PASS iff no container was OOM-killed and total restarts are within the
    threshold. Owning scenario: scenario-003 (16Mi memory limit -> exit 137)."""
    threshold = int(VERSIONS.get("RESTART_THRESHOLD", "0"))
    pods = _load(run_dir, "pods.json")
    items = pods.get("items", []) if isinstance(pods, dict) else []
    oom, restarts = [], 0
    for p in items:
        for cs in (p.get("status") or {}).get("containerStatuses") or []:
            restarts += cs.get("restartCount", 0)
            term = (cs.get("lastState") or {}).get("terminated") or {}
            if term.get("reason") == "OOMKilled":
                oom.append(f"{cs.get('name')}: exit {term.get('exitCode')}")
    if oom:
        return False, "OOMKilled — " + "; ".join(oom)
    if restarts > threshold:
        return False, f"restartCount total {restarts} > threshold {threshold}"
    return True, f"no OOMKill; restarts {restarts} <= {threshold}"


@register_scenario_check("image_build_ok", 20)
def _image_build_ok(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-010. The variant's ``docker build`` exited 0
    (run_scenario records this on meta['build_ok']; the build-failure path sets
    it False and captures build.log)."""
    ok = meta.get("build_ok") is True
    detail = ""
    if not ok:
        p = run_dir / "build.log"
        if p.exists():
            log = p.read_text(encoding="utf-8", errors="replace")
            errln = next(
                (ln.strip() for ln in reversed(log.splitlines())
                 if "returned a non-zero code" in ln or "ERROR:" in ln or "error:" in ln),
                "",
            )
            detail = f" :: {errln[:200]}" if errln else ""
    return ok, f"docker build exit {'0' if ok else 'non-zero'}{detail}"


@register_scenario_check("git_tree_resolved", 10)
def _git_tree_resolved(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-010. No file under the variant tree carries a
    Git conflict-marker line."""
    hits = _conflict_marker_files(run_dir / "tree")
    return (not hits), ("clean" if not hits else f"conflict markers in: {sorted(set(hits))}")
