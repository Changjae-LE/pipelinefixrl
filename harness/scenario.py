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
import shutil

import yaml

from harness import tools
from harness.collect import collect_all
from harness.evaluate import evaluate
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
]
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

    base_tests = REPO_ROOT / "tests"
    for f in sorted(base_tests.rglob("*")):
        if f.is_file() and not _is_transient(f):
            rel = f.relative_to(REPO_ROOT)
            g = tree / rel
            if (not g.exists()) or g.read_bytes() != f.read_bytes():
                viol.append(f"test file modified: {rel.as_posix()}")
    return viol


def _evidence_scan(run_dir: pathlib.Path, spec: dict) -> dict:
    out: dict[str, bool] = {}
    events = ""
    for name in ("events.txt", "events.json"):
        fp = run_dir / name
        if fp.exists():
            events += fp.read_text(encoding="utf-8", errors="replace")
    logs = ""
    logs_dir = run_dir / "logs"
    if logs_dir.is_dir():
        for lf in sorted(logs_dir.glob("*.log")):
            logs += lf.read_text(encoding="utf-8", errors="replace")
    for needle in spec.get("events_contains", []):
        out[f"events contains {needle!r}"] = needle in events
    for needle in spec.get("logs_contains", []):
        out[f"logs contains {needle!r}"] = needle in logs
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


def run_scenario(scenario_id: str, variant: str, enforce: bool = True):
    if variant not in ("broken", "golden"):
        raise ValueError("variant must be 'broken' or 'golden'")
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
    patch_seq = [sdir / cfg["patches"]["break"]]
    if variant == "golden":
        patch_seq.append(sdir / cfg["patches"]["golden"])
    for p in patch_seq:
        _apply_patch(tree, p)

    violations = _anticheat(tree)
    (run_dir / "anticheat.json").write_text(
        json.dumps(violations, indent=2), encoding="utf-8"
    )

    ev = cfg.get("evaluation", {}) or {}
    timeout = int(ev.get("deploy_timeout_seconds", VERSIONS.get("DEPLOY_TIMEOUT_SECONDS", "120")))

    tag = image_tag(f"{scenario_id}-{variant}", ts)
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
    }

    build_and_load(f"{scenario_id}-{variant}", tree=tree, tag=tag)

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
