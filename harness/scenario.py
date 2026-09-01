"""Scenario runner.

A scenario is a base tree plus one or two unified-diff patches:
  * broken variant  = base + break.patch
  * golden variant  = base + break.patch + golden.patch   (composes back to base)
  * baseline/advanced = base + break.patch + an agent's submitted patch

Each variant is built into its own uniquely tagged image, deployed into its own
namespace, collected, and scored by the same deterministic checks used for the
base app. The result is then compared against the per-variant expectation block
in scenario.yaml.

Tree assembly / patching lives in ``harness.patching``; anti-cheat lives in
``harness.anticheat``; check implementations live in ``harness.checks``. This
module is the run orchestrator.
"""

import json
import pathlib

import yaml

from harness import tools
from harness.anticheat import run_scenario_anticheat, universal_anticheat
from harness.checks import _SCENARIO_CHECKS, _conflict_marker_files
from harness.collect import collect_all
from harness.evaluate import (
    _base_stdout_line_count,
    _logs_are_json,
    _stdout_line_count,
    evaluate,
)
from harness.patching import (
    _TREE_PATHS,
    _apply_patch,
    _assert_frozen_subtrees,
    _copy_base_tree,
    _is_transient,
    tree_matches_base,
)
from harness.paths import RUNS_DIR, VERSIONS, ensure_state_dirs
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

# Back-compat aliases for the pre-refactor private names (kept in case anything
# external still reaches for them; nothing in-tree does).
_anticheat = universal_anticheat
_scenario_anticheat = run_scenario_anticheat

__all__ = [
    "SCENARIOS_DIR",
    "run_scenario",
    "cleanup_scenario_ns",
    "compose_check",
    "_scenario_dir",
    "_load_cfg",
    "_read_all",
    "_evidence_scan",
    "_check_expect",
    "_finish_build_failure",
    # re-exported from harness.patching so `scenmod.X` keeps working for
    # harness.agents.fix_agent and harness.cli:
    "_copy_base_tree",
    "_apply_patch",
    "_assert_frozen_subtrees",
    "_is_transient",
    "_TREE_PATHS",
    "tree_matches_base",
    # back-compat aliases:
    "_anticheat",
    "_scenario_anticheat",
]


def _scenario_dir(sid: str) -> pathlib.Path:
    return SCENARIOS_DIR / sid


def _load_cfg(sid: str) -> dict:
    return yaml.safe_load(
        (_scenario_dir(sid) / "scenario.yaml").read_text(encoding="utf-8")
    )


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

    # Anti-cheat: universal §7.2 rules always; scenario-declared rules only for a
    # candidate fix (golden / agent submission), never against the broken
    # reference which is the injected fault, not a shortcut.
    violations = universal_anticheat(tree)
    if variant in ("golden", "baseline", "advanced"):
        violations += run_scenario_anticheat(cfg, tree, loaded_tags=[tag])
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
        # (harness/checks registry hook; empty for base app / scenario-001).
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

    diffs = tree_matches_base(tree)

    base.mkdir(parents=True, exist_ok=True)
    (base / "compose-check.json").write_text(
        json.dumps({"scenario": scenario_id, "diffs": diffs}, indent=2), encoding="utf-8"
    )
    if diffs:
        print(f"compose-check {scenario_id}: FAIL — differing files: {diffs}")
        raise SystemExit(1)
    print(f"compose-check {scenario_id}: PASS — break.patch + golden.patch == base (byte-identical)")
    return diffs
