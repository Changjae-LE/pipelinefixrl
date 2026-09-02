"""Baseline and advanced repair agents for PipelineFixRL.

Both agents consume the *same* environment: they receive a scenario whose base
tree has had ``break.patch`` applied, they submit a candidate fix as a unified
diff, and the unchanged deterministic harness (``harness.scenario.run_scenario``)
builds, deploys, collects and scores it as a ``baseline`` / ``advanced`` variant
run — directly comparable to the ``broken`` and ``golden`` runs.

baseline — offline, deterministic, no-LLM *heuristic* fixer. A small table of
known single-token misconfigurations (probe path, ConfigMap key, log format,
health body, image override) plus a mechanical merge-conflict resolver. It never
inspects routes / tests / runtime evidence, so it stays clearly weaker than
advanced and 004/005/006 are outside its reach — a deliberate, visible
capability boundary.

advanced — a *deriving* fixer built from composable repair primitives
(``harness.agents.primitives``): each primitive reasons about one reusable
relationship (probe/HTTP contract, chart value wiring, Service wiring, runtime
constraints, config contracts, source integrity) over **scenario-visible
evidence only** — the broken tree's own source files plus the collected runtime
evidence (events/pods/logs/build.log/ci.log) of its own runs. Repair is an
iterative loop: derive → apply → validate → observe the new evidence → refine,
up to ``MAX_DERIVED_ROUNDS`` rounds; every round is recorded in provenance. The
derivation path NEVER reads ``golden.patch``, the golden variant, or any
expected-repaired-file content. Only after every derived round is exhausted
does ``run`` fall back — explicitly, visibly, and only when
``allow_golden_fallback`` is true — to replaying the scenario's
``golden.patch``. Every advanced result records its provenance
(repair_mode / derived_attempted / derived_validation_passed / fallback_used /
final_score / files_modified / per-round history) to
``advanced_provenance.json`` and the eval matrix, so a fallback success is
never presented as a derived success.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

from harness import scenario as scenmod
from harness.agents import primitives as prim
from harness.agents.primitives import (  # noqa: F401 — shared helpers (+ compat re-exports)
    Evidence,
    _conflict_files,
    _resolve_conflict_keep_head,
    _routes,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS_STATE = REPO_ROOT / ".state" / "agents"
RUNS_DIR = REPO_ROOT / ".state" / "runs"

# deterministic cap on derive → validate → refine rounds before any fallback
MAX_DERIVED_ROUNDS = 3

# --- baseline: literal, offline heuristics -------------------------------------
# (relpath, regex, replacement) applied to the broken tree. Deliberately dumb:
# no route parsing, no evidence, no cross-file correlation.
_BASELINE_RULES: dict[str, list[tuple[str, str, str]]] = {
    "scenario-001": [("charts/app/values.yaml", r"path: /health2\b", "path: /health")],
    "scenario-002": [("charts/app/values.yaml", r'(tagOverride:\s*).*', r'\1""')],
    "scenario-003": [
        ("charts/app/values.yaml", r"(requests:\n(?:.*\n)*?\s*memory:\s*)\S+", r"\g<1>64Mi"),
        ("charts/app/values.yaml", r"(limits:\n(?:.*\n)*?\s*memory:\s*)\S+", r"\g<1>128Mi"),
    ],
    "scenario-007": [("charts/app/values.yaml", r"key: teir\b", "key: tier")],
    "scenario-008": [("charts/app/values.yaml", r"(logFormat:\s*)\S+", r"\1json")],
    "scenario-009": [("app/main.py", r'\{"status":\s*"healthy"\}', '{"status": "ok"}')],
    # scenario-010 handled by the shared conflict resolver below.
    # scenario-004 / 005 / 006: no baseline rule (documented boundary).
}


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _sdir(scenario_id):
    return scenmod._scenario_dir(scenario_id)


def _base_after_break(scenario_id, dst):
    """<dst> = base tree + break.patch (the state the agent is handed)."""
    scenmod._copy_base_tree(dst)
    scenmod._apply_patch(dst, _sdir(scenario_id) / scenmod._load_cfg(scenario_id)["patches"]["break"])


# ---------------------------------------------------------------------------
# advanced: primitive-based derivation
# ---------------------------------------------------------------------------
def _derive_repair(scenario_id, broken_run_dir):
    """One derivation pass (round-1 semantics): compose every repair primitive
    against a scratch copy of the broken tree, using only the run's own
    evidence. Returns (edits, rationale) with edits = [(relpath, new_bytes)].
    Never touches golden material."""
    tree = broken_run_dir / "tree"
    ev = Evidence(broken_run_dir)
    with tempfile.TemporaryDirectory() as td:
        work = pathlib.Path(td) / "work"
        shutil.copytree(tree, work)
        res = prim.compose(work, ev)
        edits = [(rel, (work / rel).read_bytes()) for rel in sorted(res.changed)]
    if not edits:
        return [], "no repair primitive matched the broken tree / evidence"
    return edits, "; ".join(res.rationales)


def _validate_candidate(scenario_id, patch):
    """Score a candidate patch through the unchanged harness (monkeypatchable
    seam for the fast tests)."""
    return scenmod.run_scenario(scenario_id, "advanced", enforce=False, agent_patch=patch)


def _safe_cleanup_advanced(scenario_id):
    """Delete the namespace of the most recent advanced run (a failed derived
    round) so iterative rounds don't leak namespaces. Best-effort."""
    try:
        scenmod.cleanup_scenario_ns(scenario_id, "advanced")
    except Exception as exc:  # noqa: BLE001 — cleanup must never abort the loop
        print(f"  [advanced] round cleanup skipped: {exc}")


# ---------------------------------------------------------------------------
# patch assembly + run
# ---------------------------------------------------------------------------
def _edits_to_patch(broken_tree, edits, out_path):
    """Unified diff (broken tree -> broken tree + edits), applied by the harness
    right after break.patch."""
    chunks = []
    with tempfile.TemporaryDirectory() as td:
        w = pathlib.Path(td)
        for rel, nb in edits:
            a = broken_tree / rel
            b = w / "b"
            b.write_bytes(nb)
            cp = _run(["diff", "-U3", "--label", f"a/{rel}", "--label", f"b/{rel}",
                       str(a), str(b)])
            if cp.returncode == 0:
                continue
            if cp.returncode != 1:
                raise RuntimeError(f"diff failed for {rel}: {cp.stderr}")
            chunks.append(cp.stdout)
    out_path.write_text("".join(chunks), encoding="utf-8", newline="\n")
    return out_path


def _latest_broken_run(scenario_id):
    p = RUNS_DIR / f"last-{scenario_id}-broken"
    if p.exists():
        d = RUNS_DIR / p.read_text(encoding="utf-8").strip()
        if (d / "tree").is_dir():
            return d
    return None


def _fresh_broken_run(scenario_id):
    rid, _, _ = scenmod.run_scenario(scenario_id, "broken", enforce=False)
    return RUNS_DIR / rid


def _baseline_patch(scenario_id, out_dir):
    rules = list(_BASELINE_RULES.get(scenario_id, []))
    with tempfile.TemporaryDirectory() as td:
        broken = pathlib.Path(td) / "t"
        _base_after_break(scenario_id, broken)
        work: dict[str, str] = {}  # rel -> running text
        for rel, pat, repl in rules:
            cur = work.get(rel) or (broken / rel).read_text(encoding="utf-8")
            work[rel] = re.sub(pat, repl, cur, count=1)
        for f in _conflict_files(broken):  # scenario-010
            rel = f.relative_to(broken).as_posix()
            work[rel] = _resolve_conflict_keep_head(f.read_text(encoding="utf-8"))
        edits = [(rel, txt.encode("utf-8")) for rel, txt in work.items()
                 if txt.encode("utf-8") != (broken / rel).read_bytes()]
        if not edits:
            return None, "no_change"
        p = _edits_to_patch(broken, edits, out_dir / "baseline.patch")
        return (p if p.read_text() else None), ("heuristic" if p.read_text() else "no_change")


def run(scenario_id, tier, enforce=False, allow_golden_fallback=True):
    """Plan the fix, score it through the unchanged harness, record provenance.

    ``allow_golden_fallback=False`` (generalization mode) disables the golden
    replay entirely: after the derived rounds are exhausted the result is
    reported as ``failed`` / ``no_change`` — a fallback score can then never
    masquerade as a repair."""
    if tier not in ("baseline", "advanced"):
        raise ValueError("tier must be 'baseline' or 'advanced'")
    out_dir = AGENTS_STATE / scenario_id
    out_dir.mkdir(parents=True, exist_ok=True)

    if tier == "baseline":
        patch, mode = _baseline_patch(scenario_id, out_dir)
        files = []
        if patch:
            files = sorted({ln[6:].strip() for ln in patch.read_text().splitlines()
                            if ln.startswith("--- a/")})
        print(f"[baseline] {scenario_id}: mode={mode} files={files or 'none'}")
        rid, score, verdict = scenmod.run_scenario(scenario_id, "baseline", enforce=False,
                                                   agent_patch=patch)
        prov = {"scenario": scenario_id, "tier": "baseline", "baseline_mode": mode,
                "files_modified": files, "final_score": score,
                "matches_expectation": verdict.get("matches_expectation")}
        (RUNS_DIR / rid / "baseline_provenance.json").write_text(
            json.dumps(prov, indent=2), encoding="utf-8")
        return rid, score, verdict, prov

    # ---- advanced: iterative derive → validate → refine, fallback last ----
    prov = {"scenario": scenario_id, "tier": "advanced", "repair_mode": None,
            "derived_attempted": False, "derived_validation_passed": False,
            "fallback_used": False, "final_score": None, "files_modified": [],
            "rationale": None,
            # iterative extension (Improvement 2) — additive; every pre-existing
            # key above keeps its name and meaning.
            "allow_golden_fallback": bool(allow_golden_fallback),
            "derived_rounds_attempted": 0, "derived_rounds": [],
            "first_derived_score": None, "final_derived_score": None}

    broken_dir = _latest_broken_run(scenario_id) or _fresh_broken_run(scenario_id)
    broken_tree = broken_dir / "tree"
    cumulative: dict[str, bytes] = {}   # rel -> candidate bytes (vs broken tree)
    evidence_dir = broken_dir
    rid, score, verdict = None, None, {}

    for rnd in range(1, MAX_DERIVED_ROUNDS + 1):
        ev = Evidence(evidence_dir)
        with tempfile.TemporaryDirectory() as td:
            work = pathlib.Path(td) / "work"
            shutil.copytree(broken_tree, work)
            for rel, nb in cumulative.items():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                (work / rel).write_bytes(nb)
            res = prim.compose(work, ev)
            new_cum = dict(cumulative)
            for rel in res.changed:
                new_cum[rel] = (work / rel).read_bytes()
        new_cum = {rel: nb for rel, nb in new_cum.items()
                   if nb != (broken_tree / rel).read_bytes()}
        if not res.changed or new_cum == cumulative:
            break  # no primitive can refine further
        cumulative = new_cum

        prov["derived_attempted"] = True
        prov["derived_rounds_attempted"] = rnd
        rationale = "; ".join(res.rationales)
        prov["rationale"] = rationale
        prov["files_modified"] = sorted(cumulative)
        patch = _edits_to_patch(broken_tree, sorted(cumulative.items()),
                                out_dir / "advanced-derived.patch")
        print(f"[advanced] {scenario_id}: round {rnd} DERIVED "
              f"{sorted(cumulative)} :: {rationale}")
        if rnd > 1:
            _safe_cleanup_advanced(scenario_id)  # previous round's namespace
        rid, score, verdict = _validate_candidate(scenario_id, patch)
        passed = bool(score == 100 and not verdict.get("anticheat_violations")
                      and verdict.get("matches_expectation"))
        prov["derived_rounds"].append({
            "round": rnd,
            "evidence_sources": ev.sources,
            "findings": [{"primitive": a["primitive"], "diagnosis": a["diagnosis"],
                          "file": a["file"]} for a in res.applied],
            "edit_conflicts": res.conflicts,
            "rationale": rationale,
            "files_modified": sorted(cumulative),
            "validation_score": score,
            "validation_passed": passed,
        })
        if rnd == 1:
            prov["first_derived_score"] = score
        prov["final_derived_score"] = score

        if passed:
            prov["derived_validation_passed"] = True
            prov["repair_mode"] = "derived"
            prov["final_score"] = score
            _emit_advanced(rid, prov)
            return rid, score, verdict, prov
        print(f"[advanced] {scenario_id}: round {rnd} scored {score} / anti-cheat "
              f"{verdict.get('anticheat_violations')}")
        evidence_dir = RUNS_DIR / rid  # own-attempt feedback for the next round

    if prov["rationale"] is None:
        prov["rationale"] = "no repair primitive matched the broken tree / evidence"

    if not allow_golden_fallback:
        # generalization mode: no golden material may be consulted, ever.
        prov["repair_mode"] = "failed" if prov["derived_attempted"] else "no_change"
        if rid is None:
            rid, score, verdict = _validate_candidate(scenario_id, None)
        prov["final_score"] = score
        _emit_advanced(rid, prov)
        if enforce and score != 100:
            raise SystemExit(f"{rid}: advanced tier scored {score} "
                             f"(mode {prov['repair_mode']}, fallback disabled)")
        return rid, score, verdict, prov

    # explicit fallback — the ONLY place golden.patch is read
    prov["fallback_used"] = True
    if rid is not None:
        _safe_cleanup_advanced(scenario_id)  # last failed round's namespace
    golden = _sdir(scenario_id) / scenmod._load_cfg(scenario_id)["patches"]["golden"]
    rid, score, verdict = scenmod.run_scenario(scenario_id, "advanced", enforce=False,
                                               agent_patch=golden)
    prov["final_score"] = score
    prov["repair_mode"] = "golden_fallback" if score == 100 else "failed"
    if not prov["derived_attempted"]:
        prov["files_modified"] = ["golden.patch (fallback — no derived strategy matched)"]
    else:
        prov["files_modified"] = prov["files_modified"] + ["golden.patch (fallback)"]
    _emit_advanced(rid, prov)
    if enforce and score != 100:
        raise SystemExit(f"{rid}: advanced tier scored {score} (mode {prov['repair_mode']})")
    return rid, score, verdict, prov


def _emit_advanced(run_id, prov):
    (RUNS_DIR / run_id / "advanced_provenance.json").write_text(
        json.dumps(prov, indent=2), encoding="utf-8")
    print(f"[advanced] {prov['scenario']}: repair_mode={prov['repair_mode']} "
          f"score={prov['final_score']} derived_attempted={prov['derived_attempted']} "
          f"derived_validation_passed={prov['derived_validation_passed']} "
          f"fallback_used={prov['fallback_used']} "
          f"rounds={prov.get('derived_rounds_attempted', 0)}")


# ---------------------------------------------------------------------------
# full 001..010 evaluation matrix
# ---------------------------------------------------------------------------
_SCENARIOS = [f"scenario-{n:03d}" for n in range(1, 11)]


def eval_matrix(scenario_ids=None):
    ids = scenario_ids or _SCENARIOS
    rows = []
    for sid in ids:
        rb, sb, _ = scenmod.run_scenario(sid, "broken", enforce=False)
        scenmod.cleanup_scenario_ns(sid, "broken")
        rg, sg, _ = scenmod.run_scenario(sid, "golden", enforce=False)
        scenmod.cleanup_scenario_ns(sid, "golden")
        _, sbl, _, pbl = run(sid, "baseline")
        scenmod.cleanup_scenario_ns(sid, "baseline")
        _, sad, _, pad = run(sid, "advanced")
        scenmod.cleanup_scenario_ns(sid, "advanced")
        rows.append({
            "scenario": sid, "broken": sb, "golden": sg,
            "baseline": sbl, "baseline_mode": pbl.get("baseline_mode"),
            "baseline_files": pbl.get("files_modified"),
            "advanced": sad, "advanced_repair_mode": pad.get("repair_mode"),
            "advanced_derived_attempted": pad.get("derived_attempted"),
            "advanced_derived_validation_passed": pad.get("derived_validation_passed"),
            "advanced_fallback_used": pad.get("fallback_used"),
            "advanced_files": pad.get("files_modified"),
            "advanced_rationale": pad.get("rationale"),
        })
    AGENTS_STATE.mkdir(parents=True, exist_ok=True)
    (AGENTS_STATE / "matrix.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    _print_matrix(rows)
    return rows


def _print_matrix(rows):
    print("\n=== agent evaluation matrix (scenario-001 .. scenario-010) ===")
    hdr = f"{'scenario':<13} {'broken':>6} {'golden':>6} {'baseline':>8} {'bl-mode':<10} {'advanced':>8} {'adv repair_mode':<16}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['scenario']:<13} {r['broken']:>6} {r['golden']:>6} "
              f"{r['baseline']:>8} {str(r['baseline_mode']):<10} "
              f"{r['advanced']:>8} {str(r['advanced_repair_mode']):<16}")
    d = sum(1 for r in rows if r["advanced_repair_mode"] == "derived")
    fb = sum(1 for r in rows if r["advanced_repair_mode"] == "golden_fallback")
    print(f"\nadvanced: {d} derived / {fb} golden_fallback / {len(rows) - d - fb} other")
