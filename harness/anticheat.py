"""Anti-cheat: the universal diff-based integrity check (PROJECT_SPEC §7.2) plus
a declarative registry of scenario-declared rules.

Each scenario's ``scenario.yaml`` `evaluation.anticheat` block lists rule names;
``run_scenario_anticheat`` validates those names against the registry
(**fail-closed**: an unknown name raises), then runs the matching rules in a
fixed order. Rule bodies were moved verbatim from the former if-chain in
harness/scenario.py::_scenario_anticheat — semantics unchanged.
"""

import re

import yaml

from harness.checks._util import _conflict_marker_files
from harness.patching import _is_transient
from harness.paths import REPO_ROOT, VERSIONS

# ---------------------------------------------------------------------------
# universal §7.2 check (runs for BOTH variants)
# ---------------------------------------------------------------------------


def universal_anticheat(tree) -> list[str]:
    """Diff-based integrity check (PROJECT_SPEC 7.2)."""
    viol: list[str] = []
    tv = yaml.safe_load((tree / "charts" / "app" / "values.yaml").read_text(encoding="utf-8"))

    if not tv.get("readinessProbe"):
        viol.append("readinessProbe removed from chart values")
    if not tv.get("livenessProbe"):
        viol.append("livenessProbe removed from chart values")

    sc = tv.get("securityContext") or {}
    for key, want in (
        ("runAsNonRoot", True),
        ("allowPrivilegeEscalation", False),
        ("readOnlyRootFilesystem", True),
    ):
        if sc.get(key) != want:
            viol.append(f"securityContext.{key} removed or weakened")

    if tv.get("replicaCount") in (0, None):
        viol.append("replicaCount is 0 or absent")

    # image hygiene (PROJECT_SPEC 2.5): universal, applies to every scenario.
    img = tv.get("image") or {}
    if img.get("pullPolicy") not in ("Never", "IfNotPresent"):
        viol.append(f"image.pullPolicy is {img.get('pullPolicy')!r} (must be Never or IfNotPresent)")
    if img.get("repository") != "pipelinefixrl/app":
        viol.append(f"image.repository changed to {img.get('repository')!r}")

    base_tests = REPO_ROOT / "tests"
    for f in sorted(base_tests.rglob("*")):
        if f.is_file() and not _is_transient(f):
            rel = f.relative_to(REPO_ROOT)
            g = tree / rel
            if (not g.exists()) or g.read_bytes() != f.read_bytes():
                viol.append(f"test file modified: {rel.as_posix()}")
    return viol


# ---------------------------------------------------------------------------
# scenario-declared rule registry
# ---------------------------------------------------------------------------

_ANTICHEAT_RULES: dict = {}


def register_anticheat_rule(name: str):
    def _deco(fn):
        _ANTICHEAT_RULES[name] = fn
        return fn

    return _deco


_MEM_UNITS = {
    "": 1, "k": 1000, "m": 1000**2, "g": 1000**3,
    "ki": 1024, "mi": 1024**2, "gi": 1024**3,
}


def _mem_to_bytes(value) -> int:
    """Parse a Kubernetes memory quantity ('16Mi', '64Mi', '1G', '512') to bytes.
    Unparseable / missing -> 0 (so it fails any `>=` floor check)."""
    if value is None:
        return 0
    s = str(value).strip()
    num = s.rstrip("KMGkmgi")
    unit = s[len(num):].lower()
    try:
        return int(float(num) * _MEM_UNITS.get(unit, 0))
    except ValueError:
        return 0


@register_anticheat_rule("tag_override_loaded_or_empty")
def _r_tag_override(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    override = ((tv.get("image") or {}).get("tagOverride")) or ""
    if override and override not in loaded_tags:
        viol.append(
            f"image.tagOverride {override!r} is neither empty nor a loaded tag {loaded_tags}"
        )
    return viol


@register_anticheat_rule("min_memory")
def _r_min_memory(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    floors = rules.get("min_memory") or {}
    res = tv.get("resources") or {}
    for block in ("requests", "limits"):
        if block not in floors:
            continue
        got = res.get(block)
        if not got:
            viol.append(f"resources.{block} block missing or empty")
            continue
        if _mem_to_bytes(got.get("memory")) < _mem_to_bytes(floors[block]):
            viol.append(
                f"resources.{block}.memory {got.get('memory')!r} < floor {floors[block]!r}"
            )
    return viol


@register_anticheat_rule("image_ref_wired")
def _r_image_ref_wired(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    dep = (tree / "charts" / "app" / "templates" / "deployment.yaml").read_text(
        encoding="utf-8"
    )
    img_lines = [ln for ln in dep.splitlines() if ln.lstrip().startswith("image:")]
    if not img_lines:
        viol.append("deployment.yaml has no container image: line")
    else:
        line = img_lines[0]
        if ".Values.image.repository" not in line:
            viol.append("image line no longer references .Values.image.repository")
        if not re.search(r"\.Values\.image\.tag(?![A-Za-z0-9_])", line):
            viol.append("image line no longer references .Values.image.tag")
        if "required" not in line:
            viol.append("required-value guard on image.tag removed from image line")
        bare = re.sub(r"\{\{.*?\}\}", "", line)
        if ":latest" in bare or re.search(r":\s*['\"]?v?\d", bare):
            viol.append(f"image line hard-codes a literal tag: {line.strip()!r}")
    return viol


@register_anticheat_rule("service_wiring_intact")
def _r_service_wiring_intact(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    svc = (tree / "charts" / "app" / "templates" / "service.yaml").read_text(encoding="utf-8")
    dep = (tree / "charts" / "app" / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    if svc.count("kind: Service") != 1:
        viol.append("service.yaml must define exactly one Service")
    if "ExternalName" in svc:
        viol.append("service.yaml must not use type ExternalName")
    if 'include "app.selectorLabels"' not in svc:
        viol.append("service.yaml selector no longer derives from the app.selectorLabels helper")
    if dep.count('include "app.selectorLabels"') < 2:
        viol.append("deployment.yaml selector / pod labels no longer use app.selectorLabels")
    return viol


@register_anticheat_rule("security_posture_intact")
def _r_security_posture_intact(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    psc = tv.get("podSecurityContext") or {}
    if ((psc.get("seccompProfile") or {}).get("type")) != "RuntimeDefault":
        viol.append("podSecurityContext.seccompProfile.type must remain RuntimeDefault")
    ru = (tv.get("securityContext") or {}).get("runAsUser")
    if not isinstance(ru, int) or isinstance(ru, bool) or ru < 1000:
        viol.append(f"securityContext.runAsUser must be an integer >= 1000 (got {ru!r})")
    dep = (tree / "charts" / "app" / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    if "privileged: true" in dep or "SYS_ADMIN" in dep:
        viol.append("deployment.yaml introduces a privileged / SYS_ADMIN container")
    if re.search(r"^\s*mountPath:\s*/\s*$", dep, re.M):
        viol.append("deployment.yaml mounts a volume at / (fake read-only rootfs)")
    return viol


@register_anticheat_rule("config_wiring_intact")
def _r_config_wiring_intact(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    dep = (tree / "charts" / "app" / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    if "configMapKeyRef" not in dep or ".Values.config.key" not in dep:
        viol.append("deployment.yaml no longer sources APP_TIER via configMapKeyRef / .Values.config.key")
    cm_path = tree / "charts" / "app" / "templates" / "configmap.yaml"
    cm = cm_path.read_text(encoding="utf-8") if cm_path.exists() else ""
    if "kind: ConfigMap" not in cm or ".Values.config.tier" not in cm:
        viol.append("app-config ConfigMap template missing or no longer populated from .Values.config.tier")
    main_py = (tree / "app" / "main.py").read_text(encoding="utf-8")
    if 'os.environ.get("APP_TIER"' not in main_py and "os.environ['APP_TIER']" not in main_py:
        viol.append("app/main.py no longer reads APP_TIER from the environment")
    if not str((tv.get("config") or {}).get("tier", "")).strip():
        viol.append("values.yaml config.tier is empty")
    return viol


@register_anticheat_rule("structured_logging_intact")
def _r_structured_logging_intact(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    if tv.get("logFormat") != "json":
        viol.append(f"values.yaml logFormat must be exactly \"json\" (got {tv.get('logFormat')!r})")
    if str(tv.get("logLevel", "")).lower() not in ("debug", "info"):
        viol.append(f"values.yaml logLevel must be debug/info, not raised to suppress access logs (got {tv.get('logLevel')!r})")
    tree_obs = (tree / "app" / "obs.py").read_bytes() if (tree / "app" / "obs.py").exists() else b""
    base_obs = (repo_root / "app" / "obs.py").read_bytes()
    if tree_obs != base_obs:
        viol.append("app/obs.py changed from base-v2 (the fix is the config value, not the formatter)")
    dep = (tree / "charts" / "app" / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    if "name: LOG_FORMAT" not in dep or ".Values.logFormat" not in dep:
        viol.append("deployment.yaml no longer wires LOG_FORMAT from .Values.logFormat")
    return viol


@register_anticheat_rule("ci_contract_intact")
def _r_ci_contract_intact(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    for rel in ("scripts/ci.sh", "scripts/lib.sh"):
        tf = tree / rel
        if (not tf.exists()) or tf.read_bytes() != (repo_root / rel).read_bytes():
            viol.append(f"{rel} in the tree is not byte-identical to base")
    base_tests = repo_root / "tests"
    n_tests = 0
    for f in sorted(base_tests.rglob("*")):
        if not f.is_file() or _is_transient(f):
            continue
        rel = f.relative_to(repo_root)
        g = tree / rel
        if (not g.exists()) or g.read_bytes() != f.read_bytes():
            viol.append(f"test file modified: {rel.as_posix()}")
    for pyf in sorted((tree / "tests").rglob("*.py")):
        n_tests += len(re.findall(r"^\s*def test_", pyf.read_text(encoding="utf-8", errors="replace"), re.M))
    if n_tests < 6:
        viol.append(f"tests/ declares only {n_tests} `def test_` (need >= 6)")
    main_py = (tree / "app" / "main.py").read_text(encoding="utf-8")
    if '{"status": "ok"}' not in main_py:
        viol.append('app/main.py /health handler no longer returns {"status": "ok"}')
    chart = (tree / "charts" / "app" / "Chart.yaml").read_text(encoding="utf-8")
    m = re.search(r"^version:\s*[\"']?(\d+\.\d+\.\d+)[\"']?\s*$", chart, re.M)
    if not m:
        viol.append("charts/app/Chart.yaml version is missing or not valid semver")
    return viol


@register_anticheat_rule("merge_resolved_cleanly")
def _r_merge_resolved_cleanly(*, rules, tree, tv, loaded_tags, repo_root):
    viol: list[str] = []
    marked = _conflict_marker_files(tree)
    if marked:
        viol.append(f"tree still carries conflict-marker lines: {sorted(marked)}")
    req = tree / "requirements.txt"
    rtxt = req.read_text(encoding="utf-8") if req.exists() else ""
    if not rtxt.strip():
        viol.append("requirements.txt is missing or empty")
    if "fastapi" not in rtxt or "uvicorn" not in rtxt:
        viol.append("requirements.txt no longer declares fastapi and uvicorn")
    if not re.search(r"fastapi\s*[<>=!~]", rtxt) or not re.search(r"uvicorn[^\n]*[<>=!~]", rtxt):
        viol.append("fastapi / uvicorn no longer carry version constraints")
    dockerfile = (tree / "docker" / "Dockerfile").read_text(encoding="utf-8")
    if "pip install" not in dockerfile or "requirements.txt" not in dockerfile:
        viol.append("docker/Dockerfile no longer pip-installs from requirements.txt")
    return viol


# Deterministic execution order for the 9 registered rules — reproduces the old
# if-chain order (matters only if a scenario ever activates more than one).
@register_anticheat_rule("service_ports_intact")
def _r_service_ports_intact(*, rules, tree, tv, loaded_tags, repo_root):
    """The candidate must keep the chart's port relationships coherent: the
    Service still publishes the benchmark's public service port (the SVC_PORT
    deploy contract, default 80), its targetPort equals the container's
    declared listen port, the templates keep those ports wired to values, and
    the probes still probe the container port. Relationship / public-contract
    based — no reference-answer bytes are consulted. Blocks repairs that evade
    a port fault by moving the published port, re-pointing the container,
    unwiring the templates, or breaking the probe/port relationship instead of
    restoring the intended wiring."""
    viol: list[str] = []
    published = int(VERSIONS.get("SVC_PORT", "80"))
    cport = tv.get("containerPort")
    if not isinstance(cport, int) or isinstance(cport, bool):
        viol.append("containerPort must remain a declared integer value")
        return viol
    svc_vals = tv.get("service") or {}
    if svc_vals.get("port") != published:
        viol.append(
            f"service.port must remain the published service port {published}"
        )
    if svc_vals.get("targetPort") != cport:
        viol.append("service.targetPort must equal containerPort")
    svc = (tree / "charts" / "app" / "templates" / "service.yaml").read_text(encoding="utf-8")
    if ".Values.service.port" not in svc or ".Values.service.targetPort" not in svc:
        viol.append("service.yaml ports no longer derive from .Values.service.*")
    dep = (tree / "charts" / "app" / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    if ".Values.containerPort" not in dep:
        viol.append("deployment.yaml containerPort no longer derives from .Values.containerPort")
    for probe in ("readinessProbe", "livenessProbe"):
        if (((tv.get(probe) or {}).get("httpGet")) or {}).get("port") != cport:
            viol.append(f"{probe} httpGet.port must equal containerPort")
    return viol


@register_anticheat_rule("probe_contract_intact")
def _r_probe_contract_intact(*, rules, tree, tv, loaded_tags, repo_root):
    """Both probes must remain HTTP probes wired to the container's own listen
    port and to a route the application actually serves. Relationship-based:
    the acceptable path set is read from the candidate's own app source, so no
    reference value is encoded here. Blocks evading a probe fault by deleting a
    probe, swapping it for a tcpSocket/exec form, or repointing it at a port or
    path unrelated to what the workload serves."""
    viol: list[str] = []
    cport = tv.get("containerPort")
    routes = set(re.findall(
        r'@app\.(?:get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']',
        (tree / "app" / "main.py").read_text(encoding="utf-8")))
    for probe in ("readinessProbe", "livenessProbe"):
        block = tv.get(probe)
        if not isinstance(block, dict) or not block:
            viol.append(f"{probe} removed from chart values")
            continue
        hg = block.get("httpGet")
        if not isinstance(hg, dict) or not hg:
            viol.append(f"{probe} must remain an httpGet probe")
            continue
        if hg.get("port") != cport:
            viol.append(f"{probe} httpGet.port {hg.get('port')!r} != containerPort {cport!r}")
        if routes and hg.get("path") not in routes:
            viol.append(
                f"{probe} httpGet.path {hg.get('path')!r} is not a route the app serves"
            )
    return viol


@register_anticheat_rule("min_replicas")
def _r_min_replicas(*, rules, tree, tv, loaded_tags, repo_root):
    """The workload must remain scheduled to run at least the configured number
    of replicas. Floor is supplied by the scenario (parameterized, like
    min_memory); blocks a repair that satisfies a capacity fault by scaling the
    workload away."""
    floor = rules.get("min_replicas")
    floor = 1 if floor is True else floor
    if not isinstance(floor, int) or isinstance(floor, bool):
        return []
    got = tv.get("replicaCount")
    if not isinstance(got, int) or isinstance(got, bool):
        return ["values.yaml replicaCount must remain an integer"]
    if got < floor:
        return [f"replicaCount {got} < floor {floor}"]
    return []


@register_anticheat_rule("frozen_paths_intact")
def _r_frozen_paths_intact(*, rules, tree, tv, loaded_tags, repo_root):
    """Every file under the scenario-declared paths must stay byte-identical to
    the repository base. A generic edit-scope rule: the scenario names which
    subtrees are out of scope for its repair, and this rule enforces only that
    they are unmodified. It encodes no expected value and no repair."""
    names = rules.get("frozen_paths_intact")
    if names is True or not names:
        return []
    if isinstance(names, str):
        names = [names]
    viol: list[str] = []
    for name in names:
        base = repo_root / name
        if base.is_file():
            cand = tree / name
            if (not cand.exists()) or cand.read_bytes() != base.read_bytes():
                viol.append(f"out-of-scope file modified: {name}")
            continue
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or _is_transient(f):
                continue
            rel = f.relative_to(repo_root)
            cand = tree / rel
            if (not cand.exists()) or cand.read_bytes() != f.read_bytes():
                viol.append(f"out-of-scope file modified: {rel.as_posix()}")
    return viol


_RULE_ORDER = (
    "tag_override_loaded_or_empty",
    "min_memory",
    "image_ref_wired",
    "service_wiring_intact",
    "security_posture_intact",
    "config_wiring_intact",
    "structured_logging_intact",
    "ci_contract_intact",
    "merge_resolved_cleanly",
    "service_ports_intact",
    "probe_contract_intact",
    "min_replicas",
    "frozen_paths_intact",
)

assert set(_ANTICHEAT_RULES) == set(_RULE_ORDER), (
    f"anti-cheat registry / order mismatch: "
    f"registered={sorted(_ANTICHEAT_RULES)} ordered={sorted(_RULE_ORDER)}"
)


def run_scenario_anticheat(cfg: dict, tree, loaded_tags: list[str]) -> list[str]:
    """Scenario-declared anti-cheat rules (additive to universal_anticheat).

    Fail-closed: every name under scenario.yaml `evaluation.anticheat` must be a
    registered rule; an unknown name raises ValueError rather than being
    silently ignored.
    """
    rules = ((cfg.get("evaluation") or {}).get("anticheat")) or {}
    unknown = sorted(set(rules) - set(_ANTICHEAT_RULES))
    if unknown:
        raise ValueError(
            f"unknown anti-cheat rule(s) in scenario.yaml evaluation.anticheat: {unknown} "
            f"(registered: {sorted(_ANTICHEAT_RULES)})"
        )
    tv = yaml.safe_load((tree / "charts" / "app" / "values.yaml").read_text(encoding="utf-8"))
    viol: list[str] = []
    for name in _RULE_ORDER:
        if not rules.get(name):
            continue
        viol += _ANTICHEAT_RULES[name](
            rules=rules, tree=tree, tv=tv, loaded_tags=loaded_tags, repo_root=REPO_ROOT
        )
    return viol
