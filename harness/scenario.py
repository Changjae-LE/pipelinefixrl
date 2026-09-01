"""Milestone 2: scenario runner.

A scenario is a base tree plus one or two unified-diff patches:
  * broken variant  = base + break.patch
  * golden variant  = base + break.patch + golden.patch   (composes back to base)

Each variant is built into its own uniquely tagged image, deployed into its own
namespace, collected, and scored by the same deterministic checks used for the
base app. The result is then compared against the per-variant expectation block
in scenario.yaml.
"""

import json
import pathlib
import re
import shutil

import yaml

from harness import tools
from harness.collect import collect_all
from harness.evaluate import (
    _base_stdout_line_count,
    _logs_are_json,
    _stdout_line_count,
    evaluate,
)
from harness.paths import REPO_ROOT, RUNS_DIR, VERSIONS, ensure_state_dirs
from harness.report import write_report
from harness.run import (
    RELEASE,
    _last_pointer,
    _utc_ts,
    build_and_load,
    deploy,
    image_tag,
)

SCENARIOS_DIR = pathlib.Path(__file__).resolve().parent / "scenarios"

# Only the paths that matter for building + chart deploy + anti-cheat + compose.
_TREE_PATHS = [
    "app",
    "charts",
    "docker",
    "tests",
    "requirements.txt",
    "pyproject.toml",
    ".dockerignore",
    # scenario-009 runs the real scripts/ci.sh from inside the ephemeral tree,
    # so it (and the config/ it sources) must be present. No scenario patch is
    # allowed to touch these — enforced by _assert_frozen_subtrees below.
    "scripts",
    "config",
]

# Subtrees copied into the tree purely as tooling; a scenario break/golden patch
# must never modify them.
_FROZEN_SUBTREES = ("scripts", "config")
_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache"
)

# Transient / git-ignored artifacts that must never count as a tree difference.
_SKIP_PARTS = ("__pycache__", ".pytest_cache", ".ruff_cache")
_SKIP_SUFFIXES = (".pyc", ".pyo")


def _is_transient(path: pathlib.Path) -> bool:
    if any(part in _SKIP_PARTS or part.endswith(".egg-info") for part in path.parts):
        return True
    return path.suffix in _SKIP_SUFFIXES


def _scenario_dir(sid: str) -> pathlib.Path:
    return SCENARIOS_DIR / sid


def _load_cfg(sid: str) -> dict:
    return yaml.safe_load(
        (_scenario_dir(sid) / "scenario.yaml").read_text(encoding="utf-8")
    )


def _copy_base_tree(dst: pathlib.Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for name in _TREE_PATHS:
        src = REPO_ROOT / name
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst / name, ignore=_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst / name)


def _assert_frozen_subtrees(tree: pathlib.Path) -> None:
    """Every file under scripts/ and config/ in the ephemeral tree must be
    byte-identical to the repo — no scenario patch may touch tooling. Raises
    SystemExit on the first mismatch/missing file (both variants, not just
    golden)."""
    for name in _FROZEN_SUBTREES:
        base = REPO_ROOT / name
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or _is_transient(f):
                continue
            rel = f.relative_to(REPO_ROOT)
            g = tree / rel
            if (not g.exists()) or g.read_bytes() != f.read_bytes():
                raise SystemExit(
                    f"scenario patch modified or dropped a frozen tooling file: {rel.as_posix()}"
                )


def _apply_patch(tree: pathlib.Path, patch: pathlib.Path) -> None:
    """`patch -p1` first (byte-exact on this host), `git apply` as fallback."""
    p = str(patch.resolve())
    r = tools.run(["patch", "-p1", "--forward", "-i", p], cwd=tree, check=False)
    if r.returncode == 0:
        return
    r2 = tools.run(["git", "apply", "--verbose", p], cwd=tree, check=False)
    if r2.returncode != 0:
        raise RuntimeError(
            f"failed to apply {patch.name}\n[patch]\n{r.stdout}{r.stderr}\n"
            f"[git apply]\n{r2.stdout}{r2.stderr}"
        )


def _anticheat(tree: pathlib.Path) -> list[str]:
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


def _scenario_anticheat(cfg: dict, tree: pathlib.Path, loaded_tags: list[str]) -> list[str]:
    """Scenario-declared anti-cheat rules (additive to _anticheat / §7.2).

    Read from scenario.yaml `evaluation.anticheat`. Supported rule keys:
      tag_override_loaded_or_empty: true
          image.tagOverride must be "" or one of the tags the runner loaded.
      min_memory: {requests: <qty>, limits: <qty>}
          resources.requests/limits blocks must be present and non-empty, and
          their .memory must be >= the given floor.
      image_ref_wired: true
          deployment.yaml's container image line must still be wired to
          .Values.image.repository and .Values.image.tag, keep a `required`
          guard on the tag, and carry no hard-coded literal tag.
      service_wiring_intact: true
          service.yaml defines exactly one Service, no ExternalName, and derives
          its selector from the `app.selectorLabels` helper; deployment.yaml's
          selector / pod labels still use that same helper.
      security_posture_intact: true
          podSecurityContext.seccompProfile stays RuntimeDefault,
          securityContext.runAsUser is an integer >= 1000, and deployment.yaml
          adds no privileged/SYS_ADMIN container and no volume mounted at /.
      structured_logging_intact: true
          values.yaml logFormat is exactly "json" (no fallback-triggering
          value), logLevel is debug/info (access lines not suppressed),
          app/obs.py is byte-identical to base-v2 (the fix is the config, not
          the formatter), and deployment.yaml still wires LOG_FORMAT from
          .Values.logFormat.
      config_wiring_intact: true
          deployment.yaml still sources APP_TIER via configMapKeyRef keyed by
          .Values.config.key, the app-config ConfigMap is still templated from
          .Values.config.tier with non-empty data, app/main.py still reads
          APP_TIER from the environment, and values.yaml config.tier is non-empty.
      ci_contract_intact: true
          scripts/ci.sh and scripts/lib.sh in the tree are byte-identical to
          base, no tests/ file changed, tests/ still declares >= 6 `def test_`,
          app/main.py's /health handler returns {"status": "ok"}, and
          charts/app/Chart.yaml version is valid semver.
      merge_resolved_cleanly: true
          no file in the tree carries a conflict-marker line, requirements.txt
          is non-empty and still declares fastapi + uvicorn with version
          constraints, and docker/Dockerfile still pip-installs from
          requirements.txt.
    """
    rules = ((cfg.get("evaluation") or {}).get("anticheat")) or {}
    viol: list[str] = []
    tv = yaml.safe_load((tree / "charts" / "app" / "values.yaml").read_text(encoding="utf-8"))

    if rules.get("tag_override_loaded_or_empty"):
        override = ((tv.get("image") or {}).get("tagOverride")) or ""
        if override and override not in loaded_tags:
            viol.append(
                f"image.tagOverride {override!r} is neither empty nor a loaded tag {loaded_tags}"
            )

    floors = rules.get("min_memory") or {}
    if floors:
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

    if rules.get("image_ref_wired"):
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

    if rules.get("service_wiring_intact"):
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

    if rules.get("security_posture_intact"):
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

    if rules.get("config_wiring_intact"):
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

    if rules.get("structured_logging_intact"):
        if tv.get("logFormat") != "json":
            viol.append(f"values.yaml logFormat must be exactly \"json\" (got {tv.get('logFormat')!r})")
        if str(tv.get("logLevel", "")).lower() not in ("debug", "info"):
            viol.append(f"values.yaml logLevel must be debug/info, not raised to suppress access logs (got {tv.get('logLevel')!r})")
        tree_obs = (tree / "app" / "obs.py").read_bytes() if (tree / "app" / "obs.py").exists() else b""
        base_obs = (REPO_ROOT / "app" / "obs.py").read_bytes()
        if tree_obs != base_obs:
            viol.append("app/obs.py changed from base-v2 (the fix is the config value, not the formatter)")
        dep = (tree / "charts" / "app" / "templates" / "deployment.yaml").read_text(encoding="utf-8")
        if "name: LOG_FORMAT" not in dep or ".Values.logFormat" not in dep:
            viol.append("deployment.yaml no longer wires LOG_FORMAT from .Values.logFormat")

    if rules.get("ci_contract_intact"):
        for rel in ("scripts/ci.sh", "scripts/lib.sh"):
            tf = tree / rel
            if (not tf.exists()) or tf.read_bytes() != (REPO_ROOT / rel).read_bytes():
                viol.append(f"{rel} in the tree is not byte-identical to base")
        base_tests = REPO_ROOT / "tests"
        n_tests = 0
        for f in sorted(base_tests.rglob("*")):
            if not f.is_file() or _is_transient(f):
                continue
            rel = f.relative_to(REPO_ROOT)
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

    if rules.get("merge_resolved_cleanly"):
        from harness.evaluate import _conflict_marker_files

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


def _read_all(run_dir: pathlib.Path, *names: str) -> str:
    blob = ""
    for name in names:
        fp = run_dir / name
        if fp.exists():
            blob += fp.read_text(encoding="utf-8", errors="replace")
    return blob


def _evidence_scan(run_dir: pathlib.Path, spec: dict) -> dict:
    """Substring evidence scan. `spec` keys map to a collected artifact:
      events_contains    -> events.txt + events.json
      logs_contains      -> logs/*.log
      pods_json_contains -> pods.json
      build_log_contains -> build.log
      ci_log_contains    -> ci.log
    """
    out: dict[str, bool] = {}

    events = _read_all(run_dir, "events.txt", "events.json")
    logs = ""
    logs_dir = run_dir / "logs"
    if logs_dir.is_dir():
        for lf in sorted(logs_dir.glob("*.log")):
            logs += lf.read_text(encoding="utf-8", errors="replace")
    pods = _read_all(run_dir, "pods.json")
    build_log = _read_all(run_dir, "build.log")
    ci_log = _read_all(run_dir, "ci.log")

    sources = {
        "events_contains": ("events", events),
        "logs_contains": ("logs", logs),
        "pods_json_contains": ("pods.json", pods),
        "build_log_contains": ("build.log", build_log),
        "ci_log_contains": ("ci.log", ci_log),
    }
    for key, (label, blob) in sources.items():
        for needle in spec.get(key, []):
            out[f"{label} contains {needle!r}"] = needle in blob
    return out


def _check_expect(checks: list[dict], score: int, expect: dict, violations: list[str]) -> list[str]:
    res = {c["id"]: c["result"] for c in checks}
    problems: list[str] = []
    for cid in expect.get("must_fail", []):
        if res.get(cid) != "FAIL":
            problems.append(f"{cid}: expected FAIL, got {res.get(cid)}")
    for cid in expect.get("must_pass", []):
        if res.get(cid) != "PASS":
            problems.append(f"{cid}: expected PASS, got {res.get(cid)}")
    if "score_max" in expect and score > expect["score_max"]:
        problems.append(f"score {score} > score_max {expect['score_max']}")
    if "score_min" in expect and score < expect["score_min"]:
        problems.append(f"score {score} < score_min {expect['score_min']}")
    if expect.get("anticheat_clean") and violations:
        problems.append(f"anti-cheat violations present: {violations}")
    return problems


def _finish_build_failure(scenario_id, variant, run_id, run_dir, tree, cfg, meta, enforce):
    """The 'image never built' path (PLAN §11.4; completed for scenario-010).

    ``docker build`` failed and deploy was skipped. Score comes only from the
    registered scenario checks that need no cluster (image_build_ok,
    git_tree_resolved), using the SAME contract semantics as the normal
    run_scenario path: a real checks.json, a weighted score, an evidence scan,
    _check_expect problems, and an enforce-time SystemExit when the variant
    misses its expectation. Nothing here touches the successful build/deploy
    path.
    """
    from harness.evaluate import _SCENARIO_CHECKS, _conflict_marker_files

    conflicts = _conflict_marker_files(tree)

    ev = cfg.get("evaluation", {}) or {}
    expect = ((ev.get("expect") or {}).get(variant)) or {}
    scenario_checks = list(ev.get("checks") or [])
    weight_overrides = dict(ev.get("weights") or {})
    meta.setdefault("scenario_checks", scenario_checks)
    meta.setdefault("weight_overrides", weight_overrides)

    results: list[dict] = []
    for cid in scenario_checks:
        entry = _SCENARIO_CHECKS.get(cid)
        if entry is None:
            continue
        default_w, fn = entry
        weight = int(weight_overrides.get(cid, default_w))
        try:
            ok, reason = fn(run_dir=run_dir, namespace="", release=RELEASE, meta=meta)
        except Exception as exc:  # noqa: BLE001 - a check crash is a FAIL, not a raise
            ok, reason = False, f"check error: {exc}"
        results.append(
            {"id": cid, "weight": weight, "result": "PASS" if ok else "FAIL", "reason": reason}
        )

    denom = sum(c["weight"] for c in results) or 1
    num = sum(c["weight"] for c in results if c["result"] == "PASS")
    score = round(num / denom * 100)
    (run_dir / "checks.json").write_text(
        json.dumps({"checks": results, "score": score}, indent=2), encoding="utf-8"
    )

    violations = meta.get("anticheat_violations") or []
    evidence = _evidence_scan(run_dir, expect.get("evidence", {}) or {})
    problems = _check_expect(results, score, expect, violations)
    if expect.get("evidence"):
        missing = [k for k, okk in evidence.items() if not okk]
        if missing:
            problems.append(f"missing required evidence: {missing}")

    verdict = {
        "run_id": run_id,
        "scenario": scenario_id,
        "variant": variant,
        "score": score,
        "build_ok": False,
        "conflict_marker_files": conflicts,
        "expected": expect,
        "evidence": evidence,
        "anticheat_violations": violations,
        "expectation_problems": problems,
        "matches_expectation": not problems,
    }
    (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (run_dir / "report.txt").write_text(
        f"{run_id}\nBUILD FAILED — deploy skipped.\nSCORE: {score}\n"
        f"conflict markers in: {conflicts or 'none'}\n"
        + "".join(
            f"{c['id']:<20} {c['weight']:>3}  {c['result']}  {c['reason']}\n" for c in results
        ),
        encoding="utf-8",
    )
    _last_pointer(f"{scenario_id}-{variant}").write_text(run_id, encoding="utf-8")
    print(
        f"{run_id}: BUILD FAILED (deploy skipped); SCORE {score}; "
        f"conflict markers in {conflicts or 'none'}"
    )
    if enforce and problems:
        raise SystemExit(f"{run_id}: expectation not met -> {problems}")
    return run_id, score, verdict


def run_scenario(scenario_id: str, variant: str, enforce: bool = True, agent_patch=None):
    if variant not in ("broken", "golden", "baseline", "advanced"):
        raise ValueError("variant must be 'broken', 'golden', 'baseline', or 'advanced'")
    ensure_state_dirs()
    cfg = _load_cfg(scenario_id)
    sdir = _scenario_dir(scenario_id)
    ts = _utc_ts()
    run_id = f"{scenario_id}-{variant}-{ts}"
    namespace = f"pfrl-{scenario_id}-{variant}-{ts}".lower()
    run_dir = RUNS_DIR / run_id
    tree = run_dir / "tree"
    run_dir.mkdir(parents=True, exist_ok=True)

    _copy_base_tree(tree)
    # Every non-broken variant is a *candidate fix* applied on top of the same
    # injected fault: golden = the reference patch; baseline / advanced = an
    # agent's submitted patch (None => the agent submitted no change).
    patch_seq = [sdir / cfg["patches"]["break"]]
    if variant == "golden":
        patch_seq.append(sdir / cfg["patches"]["golden"])
    elif variant in ("baseline", "advanced") and agent_patch is not None:
        patch_seq.append(pathlib.Path(agent_patch))
    for p in patch_seq:
        _apply_patch(tree, p)
    _assert_frozen_subtrees(tree)

    ev = cfg.get("evaluation", {}) or {}
    timeout = int(ev.get("deploy_timeout_seconds", VERSIONS.get("DEPLOY_TIMEOUT_SECONDS", "120")))
    tag = image_tag(f"{scenario_id}-{variant}", ts)

    # Anti-cheat: base §7.2 rules always; scenario-declared rules only for a
    # candidate fix (golden / agent submission), never against the broken
    # reference which is the injected fault, not a shortcut.
    violations = _anticheat(tree)
    if variant in ("golden", "baseline", "advanced"):
        violations += _scenario_anticheat(cfg, tree, loaded_tags=[tag])
    (run_dir / "anticheat.json").write_text(
        json.dumps(violations, indent=2), encoding="utf-8"
    )

    meta = {
        "run_id": run_id,
        "scenario": scenario_id,
        "variant": variant,
        "namespace": namespace,
        "image": tag,
        "started_at": ts,
        "cluster": VERSIONS["KIND_CLUSTER_NAME"],
        "deploy_timeout_seconds": timeout,
        "patches_applied": [p.name for p in patch_seq],
        "anticheat_violations": violations,
        "loaded_tags": [tag],
        # activate scenario-specific scored checks + weight reallocation
        # (harness/evaluate.py registry hook; empty for base app / scenario-001).
        "scenario_checks": list(ev.get("checks") or []),
        "weight_overrides": dict(ev.get("weights") or {}),
    }

    # Build path with a build-failure branch (fully exercised by scenario-010):
    # on failure, capture build.log, skip deploy, and score from build/git checks.
    try:
        build_and_load(f"{scenario_id}-{variant}", tree=tree, tag=tag)
        meta["build_ok"] = True
    except Exception as e:  # noqa: BLE001 - any build/load failure routes here
        meta["build_ok"] = False
        cap = tools.run(
            ["docker", "build", "-f", str(tree / "docker" / "Dockerfile"), "-t", tag, str(tree)],
            check=False,
        )
        (run_dir / "build.log").write_text(
            (cap.stdout or "") + (cap.stderr or "") + f"\n[exception] {e}\n",
            encoding="utf-8",
        )
        return _finish_build_failure(scenario_id, variant, run_id, run_dir, tree, cfg, meta, enforce)

    node = f"{VERSIONS['KIND_CLUSTER_NAME']}-control-plane"
    imgs = tools.run(["docker", "exec", node, "crictl", "images"], check=False)
    (run_dir / "node-images.txt").write_text(imgs.stdout or imgs.stderr or "", encoding="utf-8")
    meta["image_on_node"] = tag.split(":", 1)[0] in (imgs.stdout or "")

    meta.update(deploy(tag, namespace, tree=tree, timeout=timeout))
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    collect_all(namespace, RELEASE, run_dir)
    checks, score = evaluate(namespace, RELEASE, run_dir, meta)

    expect = ((ev.get("expect") or {}).get(variant)) or {}
    evidence = _evidence_scan(run_dir, expect.get("evidence", {}) or {})
    problems = _check_expect(checks, score, expect, violations)
    if expect.get("evidence"):
        missing = [k for k, ok in evidence.items() if not ok]
        if missing:
            problems.append(f"missing required evidence: {missing}")

    # Observability signals (scenario-008 / M9 contract; additive keys, harmless
    # for other scenarios). When structured_logs_ok ran it recorded its own
    # measurements on meta -> use those so verdict.json and the check agree on
    # one metric; otherwise fall back to the collected-log snapshot.
    _sl = meta.get("structured_logs") or {}
    verdict = {
        "run_id": run_id,
        "scenario": scenario_id,
        "variant": variant,
        "score": score,
        "expected": expect,
        "evidence": evidence,
        "anticheat_violations": violations,
        "expectation_problems": problems,
        "matches_expectation": not problems,
        "logs_are_json": _sl.get("logs_are_json", _logs_are_json(run_dir)),
        "stdout_line_count": _sl.get("stdout_line_count", _stdout_line_count(run_dir)),
        "base_stdout_line_count": _sl.get("base_stdout_line_count", _base_stdout_line_count()),
    }
    (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")

    text = write_report(run_dir, meta, checks, score)
    print(text)
    print(f"patches      : {meta['patches_applied']}")
    print(f"anti-cheat   : {'clean' if not violations else violations}")
    print(f"evidence     : {evidence}")
    print(
        "expectation  : "
        + ("MATCH" if not problems else f"MISMATCH -> {problems}")
    )

    _last_pointer(f"{scenario_id}-{variant}").write_text(run_id, encoding="utf-8")

    if enforce and problems:
        raise SystemExit(f"{run_id}: expectation not met -> {problems}")
    return run_id, score, verdict


def cleanup_scenario_ns(scenario_id: str, variant: str) -> str:
    run_id = _last_pointer(f"{scenario_id}-{variant}").read_text(encoding="utf-8").strip()
    meta = json.loads((RUNS_DIR / run_id / "meta.json").read_text(encoding="utf-8"))
    ns = meta["namespace"]
    tools.kubectl(
        ["delete", "namespace", ns, "--ignore-not-found=true", "--wait=true", "--timeout=120s"],
        check=False,
    )
    return ns


def compose_check(scenario_id: str):
    """Prove break.patch + golden.patch applied to base == base, byte for byte."""
    cfg = _load_cfg(scenario_id)
    sdir = _scenario_dir(scenario_id)
    ts = _utc_ts()
    base = RUNS_DIR / f"{scenario_id}-compose-{ts}"
    tree = base / "tree"
    _copy_base_tree(tree)
    _apply_patch(tree, sdir / cfg["patches"]["break"])
    _apply_patch(tree, sdir / cfg["patches"]["golden"])

    diffs: list[str] = []
    for name in _TREE_PATHS:
        src = REPO_ROOT / name
        if not src.exists():
            continue
        if src.is_file():
            if src.read_bytes() != (tree / name).read_bytes():
                diffs.append(name)
            continue
        for f in sorted(src.rglob("*")):
            if f.is_file() and not _is_transient(f):
                rel = f.relative_to(REPO_ROOT)
                g = tree / rel
                if (not g.exists()) or g.read_bytes() != f.read_bytes():
                    diffs.append(rel.as_posix())

    base.mkdir(parents=True, exist_ok=True)
    (base / "compose-check.json").write_text(
        json.dumps({"scenario": scenario_id, "diffs": diffs}, indent=2), encoding="utf-8"
    )
    if diffs:
        print(f"compose-check {scenario_id}: FAIL — differing files: {diffs}")
        raise SystemExit(1)
    print(f"compose-check {scenario_id}: PASS — break.patch + golden.patch == base (byte-identical)")
    return diffs
