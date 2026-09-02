"""Held-out generalization evaluation (benchmark/evaluation side).

Runs the FROZEN advanced agent (freeze commit AGENT_FREEZE_COMMIT) against the
held-out scenarios with the golden fallback disabled, under an active
golden-access guard. A golden-fallback score can therefore never masquerade as
generalization success; the outcomes are exactly derived / failed / no_change.

Official first-shot protocol (per held-out scenario):
    broken run (contract enforced)
      -> frozen advanced, allow_golden_fallback=False, golden-access guard on
      -> raw result archived
      -> ONLY AFTER the archive: golden validation (separate step, see
         golden_validate / the --golden-check CLI flag).
Never broken -> golden -> advanced: the agent must not have golden runtime
artifacts available anywhere when it derives.

This module lives outside harness/agents/** — the frozen repair implementation
is imported, never modified.
"""

from __future__ import annotations

import builtins
import contextlib
import datetime
import hashlib
import json
import pathlib

from harness import scenario as scenmod
from harness.paths import REPO_ROOT

AGENT_FREEZE_COMMIT = "8ccbe0d62df1c336e2384d45486db52194630892"
HELD_OUT_IDS = ["h01", "h02", "h03"]
# Honest novelty classification, fixed at design-review time (A = held-out
# instance of a relationship the frozen primitives already represent; B =
# genuinely novel relationship with no dedicated frozen repair branch).
NOVELTY = {"h01": "A", "h02": "A", "h03": "B"}
AGENTS_STATE = REPO_ROOT / ".state" / "agents"
EVIDENCE_PATH = REPO_ROOT / "docs" / "evidence" / "generalization-first-shot.json"


@contextlib.contextmanager
def golden_access_guard():
    """While active, ANY Python-side read of a path whose name contains
    'golden' raises — held-out golden.patch, golden trees, golden run
    directories, everything. Wrapped around the frozen agent's entire
    derivation + validation so no expected-repair material can leak in."""
    real_rt = pathlib.Path.read_text
    real_rb = pathlib.Path.read_bytes
    real_popen = pathlib.Path.open
    real_open = builtins.open

    def _check(target):
        if "golden" in str(target).lower():
            raise RuntimeError(
                f"GOLDEN ACCESS BLOCKED during generalization derivation: {target}"
            )

    def guarded_rt(self, *a, **k):
        _check(self)
        return real_rt(self, *a, **k)

    def guarded_rb(self, *a, **k):
        _check(self)
        return real_rb(self, *a, **k)

    def guarded_popen(self, *a, **k):
        _check(self)
        return real_popen(self, *a, **k)

    def guarded_open(file, *a, **k):
        _check(file)
        return real_open(file, *a, **k)

    pathlib.Path.read_text = guarded_rt
    pathlib.Path.read_bytes = guarded_rb
    pathlib.Path.open = guarded_popen
    builtins.open = guarded_open
    try:
        yield
    finally:
        pathlib.Path.read_text = real_rt
        pathlib.Path.read_bytes = real_rb
        pathlib.Path.open = real_popen
        builtins.open = real_open


def _select(only=None):
    if not only:
        return list(HELD_OUT_IDS)
    ids = [s.strip() for s in (only.split(",") if isinstance(only, str) else only) if s.strip()]
    unknown = sorted(set(ids) - set(HELD_OUT_IDS))
    if unknown:
        raise ValueError(f"unknown held-out id(s): {unknown} (valid: {HELD_OUT_IDS})")
    return ids


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scenario_hashes(sid: str) -> dict:
    """Pin exactly which scenario definition the result was produced against.
    Benchmark bookkeeping — computed AFTER derivation, never inside the guard."""
    sdir = scenmod._scenario_dir(sid)
    return {
        "scenario.yaml": _sha256(sdir / "scenario.yaml"),
        "break.patch": _sha256(sdir / "break.patch"),
        "golden.patch": _sha256(sdir / "golden.patch"),
    }


def row_from_provenance(sid: str, broken_score, prov: dict) -> dict:
    derived_success = bool(
        prov.get("repair_mode") == "derived" and prov.get("final_score") == 100
        and prov.get("fallback_used") is False
    )
    return {
        "scenario": sid,
        "novelty": NOVELTY.get(sid),
        "broken": broken_score,
        "first_derived_score": prov.get("first_derived_score"),
        "final_derived_score": prov.get("final_derived_score"),
        "derived_rounds_attempted": prov.get("derived_rounds_attempted"),
        "derived_rounds": prov.get("derived_rounds"),
        "repair_mode": prov.get("repair_mode"),
        "fallback_used": prov.get("fallback_used"),
        "final_score": prov.get("final_score"),
        "files_modified": prov.get("files_modified"),
        "rationale": prov.get("rationale"),
        "derived_success": derived_success,
    }


def run_first_shot(only=None):
    """Broken contract, then the frozen agent with the fallback disabled and
    the golden-access guard armed. NO golden variant runs here."""
    from harness.agents import fix_agent  # frozen implementation — imported only

    ids = _select(only)
    rows = []
    for sid in ids:
        print(f"\n=== generalization {sid}: broken contract ===")
        _, broken_score, _ = scenmod.run_scenario(sid, "broken", enforce=True)
        scenmod.cleanup_scenario_ns(sid, "broken")

        print(f"=== generalization {sid}: FROZEN advanced, fallback DISABLED ===")
        try:
            with golden_access_guard():
                _, _, _, prov = fix_agent.run(sid, "advanced", allow_golden_fallback=False)
        except Exception as exc:  # noqa: BLE001 — a crash is an honest failure, not an abort
            print(f"  [generalization] {sid}: frozen agent CRASHED: {exc}")
            prov = {"repair_mode": "failed", "fallback_used": False,
                    "derived_rounds_attempted": 0, "derived_rounds": [],
                    "first_derived_score": None, "final_derived_score": None,
                    "final_score": None, "files_modified": [],
                    "rationale": f"frozen agent crashed: {exc}"}
        try:
            scenmod.cleanup_scenario_ns(sid, "advanced")
        except Exception as exc:  # noqa: BLE001 — cleanup must not mask results
            print(f"  [generalization] cleanup skipped: {exc}")
        rows.append(row_from_provenance(sid, broken_score, prov))

    AGENTS_STATE.mkdir(parents=True, exist_ok=True)
    (AGENTS_STATE / "generalization.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8")
    print_matrix(rows)
    return rows


def golden_validate(only=None):
    """Post-archive step: each held-out golden must score 100 with anti-cheat
    clean, plus the compose round-trip. Run ONLY after the first-shot result
    has been archived."""
    for sid in _select(only):
        scenmod.run_scenario(sid, "golden", enforce=True)
        scenmod.cleanup_scenario_ns(sid, "golden")
        scenmod.compose_check(sid)
    print("held-out golden validation: PASS")


def build_first_shot_artifact(rows) -> dict:
    n = len(rows)
    succ = sum(1 for r in rows if r["derived_success"])
    return {
        "artifact": "official held-out generalization first-shot result",
        "agent_freeze_commit": AGENT_FREEZE_COMMIT,
        "protocol": (
            "per scenario: broken (contract enforced) -> frozen advanced with "
            "allow_golden_fallback=False under an active golden-access guard -> "
            "archived before any held-out golden run; golden fallback can never "
            "count as generalization success"
        ),
        "generated_utc": datetime.datetime.now(datetime.UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenarios": [dict(r, scenario_hashes=scenario_hashes(r["scenario"]))
                      for r in rows],
        "generalization_success": f"{succ}/{n} derived",
        "generalization_rate": (succ / n) if n else None,
    }


def write_first_shot_artifact(rows, out_path=None):
    """Write the tracked official evidence artifact. Must be produced from
    exactly one official first-shot run and never regenerated to improve the
    reported number."""
    out = pathlib.Path(out_path) if out_path else EVIDENCE_PATH
    if out.exists():
        raise SystemExit(
            f"REFUSED: official first-shot artifact already exists at {out} — "
            "it must come from exactly one official run and is never overwritten")
    out.parent.mkdir(parents=True, exist_ok=True)
    art = build_first_shot_artifact(rows)
    out.write_text(json.dumps(art, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"first-shot evidence artifact written: {out}")
    return out


def print_matrix(rows):
    print("\n=== held-out generalization matrix (frozen agent, fallback disabled) ===")
    hdr = (f"{'scenario':<9} {'nov':<3} {'broken':>6} {'first':>5} {'final':>5} "
           f"{'rounds':>6} {'mode':<10} {'fallback':>8} {'derived_success':>15}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['scenario']:<9} {str(r['novelty']):<3} {r['broken']:>6} "
              f"{str(r['first_derived_score']):>5} {str(r['final_derived_score']):>5} "
              f"{str(r['derived_rounds_attempted']):>6} {str(r['repair_mode']):<10} "
              f"{str(r['fallback_used']):>8} {str(r['derived_success']):>15}")
    succ = sum(1 for r in rows if r["derived_success"])
    print(f"\nheld-out generalization: {succ} / {len(rows)} derived "
          f"(golden fallback disabled; this number is separate from the "
          f"scenario-001..010 'advanced 10/10 derived' benchmark)")
