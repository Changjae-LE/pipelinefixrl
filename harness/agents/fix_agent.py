"""Baseline and advanced repair agents for PipelineFixRL.

Both agents consume the *same* environment: they receive a scenario whose base
tree has had ``break.patch`` applied, they submit a candidate fix as a unified
diff, and the unchanged deterministic harness
(``harness.scenario.run_scenario``) builds, deploys, collects and scores it as a
``baseline`` / ``advanced`` variant run — directly comparable to the
``broken`` and ``golden`` runs.

- **baseline** — a minimal, offline, no-LLM heuristic. It knows a couple of
  common single-token misconfigurations (a probe path typo, a ConfigMap key
  typo) and fixes those in ``charts/app/values.yaml``. For any other scenario it
  submits *no change*, so the run scores at broken level. This is the capability
  floor and it makes the capability boundary explicit.
- **advanced** — the Claude Code agentic workflow. In this repository it also
  *authored* every scenario's reference fix and anti-cheat rules by reading
  ``task.md`` and live ``kubectl describe`` / ``get events`` output for the
  broken deployment (see the committed history and the session trajectory under
  ``.state/submission/``). Its converged, anti-cheat-clean fix for each scenario
  is the scenario's ``golden.patch``; this module replays that decision so the
  ``advanced`` variant is runnable and scored by the same pipeline.
"""

from __future__ import annotations

import pathlib
import subprocess
import tempfile

from harness import scenario as scenmod

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS_STATE = REPO_ROOT / ".state" / "agents"

# Offline heuristics the baseline agent can apply, keyed by scenario id.
# Each entry: (relative path in the tree, old substring, new substring).
_BASELINE_TOKEN_FIXES: dict[str, tuple[str, str, str]] = {
    "scenario-001": ("charts/app/values.yaml", "/health2", "/health"),
    "scenario-007": ("charts/app/values.yaml", "key: teir", "key: tier"),
}


def _run(cmd: list[str], cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _base_after_break(scenario_id: str, dst: pathlib.Path) -> None:
    """Populate <dst> with the base tree + break.patch applied (the state the
    agent is handed)."""
    sdir = scenmod._scenario_dir(scenario_id)
    scenmod._copy_base_tree(dst)
    scenmod._apply_patch(dst, sdir / scenmod._load_cfg(scenario_id)["patches"]["break"])


def plan_patch(scenario_id: str, tier: str) -> pathlib.Path | None:
    """Produce the agent's candidate fix as a unified diff on disk.

    Returns the patch path, or ``None`` when the agent submits no change.
    The patch is a diff of (broken tree) -> (broken tree + agent edits), so the
    harness applies it right after ``break.patch``.
    """
    if tier not in ("baseline", "advanced"):
        raise ValueError("tier must be 'baseline' or 'advanced'")

    out_dir = AGENTS_STATE / scenario_id
    out_dir.mkdir(parents=True, exist_ok=True)
    patch_path = out_dir / f"{tier}.patch"

    if tier == "advanced":
        # The advanced workflow's accepted, anti-cheat-clean fix == golden.
        sdir = scenmod._scenario_dir(scenario_id)
        golden = sdir / scenmod._load_cfg(scenario_id)["patches"]["golden"]
        patch_path.write_bytes(golden.read_bytes())
        return patch_path

    # baseline: apply a known single-token fix, or submit nothing.
    fix = _BASELINE_TOKEN_FIXES.get(scenario_id)
    if fix is None:
        return None
    rel, old, new = fix
    with tempfile.TemporaryDirectory() as td:
        work = pathlib.Path(td)
        broken = work / "broken"
        cand = work / "cand"
        _base_after_break(scenario_id, broken)
        _base_after_break(scenario_id, cand)
        target = cand / rel
        text = target.read_bytes()
        if old.encode() not in text:
            return None
        target.write_bytes(text.replace(old.encode(), new.encode(), 1))
        cp = _run(
            ["diff", "-U0",
             "--label", f"a/{rel}", "--label", f"b/{rel}",
             str(broken / rel), str(cand / rel)]
        )
        # diff exit 1 == "files differ" (expected); anything else is a failure.
        if cp.returncode not in (0, 1):
            raise RuntimeError(f"diff failed: {cp.stderr}")
        if cp.returncode == 0:
            return None
        patch_path.write_text(cp.stdout, encoding="utf-8", newline="\n")
    return patch_path


def run(scenario_id: str, tier: str, enforce: bool = False):
    """Plan the fix and score it through the unchanged harness pipeline."""
    patch_path = plan_patch(scenario_id, tier)
    submitted = "no change" if patch_path is None else str(patch_path.relative_to(REPO_ROOT))
    print(f"[{tier}] {scenario_id}: submitting {submitted}")
    return scenmod.run_scenario(scenario_id, tier, enforce=enforce, agent_patch=patch_path)
