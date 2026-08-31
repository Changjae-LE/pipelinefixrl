# PipelineFixRL — Submission Notes (deadline snapshot 2026-08-31)

## 1. Implementation commit status — COMPLETE (through M8)

Working tree: **clean**. Branch: `master`. HEAD: `68269af`.

Commit chain since the scenario-005 PLAN commit (`22eb6c1`):

| Commit    | Message                                                           |
|-----------|------------------------------------------------------------------|
| `9492bdc` | feat: add deterministic runs-as-root pod-security scenario  (scenario-006 / M7) |
| `4c7a559` | docs: mark scenario-006 M7 complete                              |
| `7393766` | feat: add deterministic misconfigured ConfigMap reference scenario  (scenario-007 / M8) |
| `68269af` | docs: mark scenario-007 M8 complete                             |

Full history: `67c767f` (M0+M1) … `68269af` (M8). 20 commits.

### Scenario-006 / M7 (committed, fully validated)
- Fault: `charts/app/values.yaml` `securityContext` weakened (runAsNonRoot false,
  runAsUser 0, allowPrivilegeEscalation true, readOnlyRootFilesystem false,
  capabilities.drop []). Golden = exact reverse.
- New checks: `runs_as_nonroot` (15), `readonly_rootfs` (10), `no_priv_escalation`
  (10), `caps_dropped` (10) — append-only in `harness/evaluate.py`.
- New anti-cheat rule: `security_posture_intact` (golden-only) in
  `harness/scenario.py::_scenario_anticheat`.
- Runtime acceptance: broken ×2 deterministic **SCORE 55** (7 functional PASS /
  4 posture FAIL); golden **SCORE 100**; compose-check byte-identical.
- Post-impl regression: A1–A14 + scenario-001..006 all green.

### Scenario-007 / M8 (committed, fully validated)
- Fault: `charts/app/values.yaml` `config.key` `tier`→`teir` → container
  `APP_TIER` `configMapKeyRef` misses → `CreateContainerConfigError`. Golden =
  1-line reverse.
- New check: `config_applied` (15) + `_http_get_json` helper — append-only in
  `harness/evaluate.py`. Validates values → ConfigMap → configMapKeyRef → env →
  `GET /` `tier` chain.
- New anti-cheat rule: `config_wiring_intact` (golden-only) in
  `harness/scenario.py::_scenario_anticheat`.
- Runtime acceptance: broken ×2 deterministic **SCORE 10** (only helm_release_ok
  PASS; evidence: `couldn't find key` + `app-config` in events,
  `CreateContainerConfigError` in pods.json); golden **SCORE 100**
  (`config_applied`: `GET / tier='standard' want='standard'`); compose-check
  byte-identical.
- Post-implementation regression (commit `7393766`): A1–A14 (`make e2e-base`
  SCORE 100) + scenario-001..007 broken/golden/compose all PASS
  (broken scores 10/10/10/10/50/55/10; golden all 100; every expectation MATCH).

### NOT done (deadline cut)
- scenario-008 / M9 (structured-log format regression)
- scenario-009 / M10 (CI + health contract regression)
- scenario-010 / M11 (unresolved merge conflict)
- `docs/PLAN.md` §11.8 "Next:" pointer currently reads "M9 / scenario-008 — not started."

## 2. Push status — BLOCKED (no git remote configured)

`git remote -v` is empty; `.git/config` has no `[remote]` section. Nothing has
ever been pushed/fetched. A remote must be added before any push is possible.

## 3. What to submit

The **git repository** at `C:\micro`, branch `master`, HEAD `68269af`, clean tree.
Portable copies were generated in `C:\micro\.state\submission\`:

- `pipelinefixrl-full.bundle` — complete verified git bundle (all history + all
  refs). `git clone pipelinefixrl-full.bundle <dir>` reconstructs the repo.
- `M7-M8.patch` — `git format-patch` series `22eb6c1..HEAD` (the 4 new commits);
  verified it applies cleanly onto `22eb6c1`.
- `agent-trajectory.jsonl` — full Claude Code session trace (this build session).
- `823de8b7-...jsonl` — same trace, original filename.

## 4. Exact submission steps

### A. If submitting by pushing to a Git remote (GitHub/GitLab)
```
cd /c/micro
git remote add origin <REMOTE_URL>          # e.g. git@github.com:<you>/pipelinefixrl.git
git push -u origin master
```
Then submit the remote URL + commit hash `68269af` through the micro1 hackathon
submission form.

### B. If submitting an archive / bundle
Upload `C:\micro\.state\submission\pipelinefixrl-full.bundle`
(recipient: `git clone pipelinefixrl-full.bundle pipelinefixrl && cd pipelinefixrl && git log --oneline`).

### C. Agent trajectory / trace
Attach `C:\micro\.state\submission\agent-trajectory.jsonl` (7.2 MB) wherever the
submission asks for the agent trace/transcript.

## 5. Verification one-liners
```
cd /c/micro
git status --porcelain           # -> empty (clean)
git rev-parse HEAD               # -> 68269af...
git log --oneline 22eb6c1..HEAD  # -> 4 commits (M7 feat/docs, M8 feat/docs)
git bundle verify .state/submission/pipelinefixrl-full.bundle
```
