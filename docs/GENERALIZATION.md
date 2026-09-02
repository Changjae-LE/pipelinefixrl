# Generalization Evaluation

## Goal

`advanced 10/10 derived` on scenario-001…010 proves the agent can repair the
faults it was **developed against** — recall, not generalization. Every
detector-era heuristic was written while looking at those ten faults, so a
perfect score there cannot distinguish "general repair capability" from
"memorized the benchmark". This evaluation produces the missing evidence: a
**first-shot** run of a **frozen** agent against scenarios that did not exist
when the agent was last modified, with the golden fallback disabled so a replay
of the reference answer can never masquerade as a repair.

## Agent Freeze

- **Freeze commit: `8ccbe0d62df1c336e2384d45486db52194630892`**
  (*feat: primitive-based iterative deriving fixer (agent freeze for held-out
  eval)*).
- The held-out scenarios (`harness/scenarios/held-out/h01..h03`) were authored
  **only after** that commit; no held-out material of any kind existed at
  freeze time (verified and recorded in the freeze commit message).
- `harness/agents/**` remained **byte-identical** to the freeze commit through
  every subsequent phase — verified with
  `git diff 8ccbe0d… -- harness/agents` before and after each phase, and
  enforced continuously by a fast test
  (`tests_meta/agents/test_no_heldout_leakage.py`), together with a static scan
  proving no held-out id (`h01`/`h02`/`h03`/`held-out`) appears anywhere in the
  frozen repair implementation.

## Held-Out Scenario Taxonomy

| scenario | fault | novelty | closest existing relationship | result |
|---|---|---|---|---|
| h01 | Service `targetPort: 9090` while the container listens on 8000 — selector correct, pod Ready, endpoints ready, traffic dead | **A** | scenario-005 breaks the *selector* leg (no endpoints); h01 breaks the *port* leg of the same Service relationship (endpoints ready) | **100 derived** (round 1) |
| h02 | memory request+limit 64Gi — valid manifest no node can schedule; `FailedScheduling`, pod Pending forever, no container start | **A** | scenario-003 is the inverse life-stage: pod runs then is OOMKilled; h02 never schedules | **100 derived** (round 1) |
| h03 | `service.port: 8081` — the Service stops publishing the public contract port 80; backend fully healthy | **B** | no existing scenario or frozen repair branch involves the *published* port contract | **65 `no_change`** — first-shot failure |

**Type A vs Type B, precisely:** the frozen primitives were built (before any
held-out scenario existed) around general relationship categories — including
targetPort↔containerPort and Unschedulable-request reasoning. h01 and h02 are
therefore **held-out *instances* of general relationships the frozen
primitives already represent**: new concrete faults the agent had never seen,
but of a shape it was equipped to reason about. They must not be described as
"unseen fault classes". Only **h03 is a genuinely novel Type B relationship**:
no frozen primitive contains any reasoning about the Service's published port
vs the benchmark's public `SVC_PORT` contract.

## Official First-Shot Protocol

Executed **exactly once**, per scenario, in this order:

```
broken run (contract enforced)
  → FROZEN advanced, allow_golden_fallback=False, golden-access guard armed
  → raw result archived to docs/evidence/generalization-first-shot.json
  → ONLY AFTER the archive: golden validation (score 100, anti-cheat clean,
    compose identity)
```

Never `broken → golden → advanced`: no golden runtime artifact existed anywhere
when the frozen agent derived.

- **Golden-access guard** (`harness/generalization.py`): while the frozen agent
  runs, every Python-side read of any path containing `golden`
  (`Path.read_text`/`read_bytes`/`Path.open`/`builtins.open`) raises — held-out
  `golden.patch`, golden trees, golden run directories, expected repaired
  bytes are all unreachable.
- **Fallback disabled**: outcomes are exactly `derived` / `no_change` /
  `failed`. `golden_fallback` is impossible in this mode and would count as a
  generalization **failure** if it ever appeared.
- **No tuning after observing results**: the agent, the held-out definitions,
  the checks and the anti-cheat were not modified after the first-shot result
  was seen. An ordinary agent failure is a valid benchmark result and is
  recorded as such.
- The artifact writer **refuses to overwrite** an existing official artifact.

## Results

**Overall: 2/3 derived · Type A: 2/2 · Type B: 0/1 · golden_fallback: 0**

| scenario | novelty | broken | first score | final score | rounds | repair_mode | fallback |
|---|---|---|---|---|---|---|---|
| h01 | A | 65 | 100 | 100 | 1 | `derived` | false |
| h02 | A | 10 | 100 | 100 | 1 | `derived` | false |
| h03 | B | 65 | — | — | 0 | `no_change` | false |

Both successes were **first-round** repairs; the iterative refinement loop
(max 3 rounds) was available but not needed in these production runs — it is
exercised by the fast test suite. h01 was repaired by the frozen
`service_wiring` primitive (targetPort re-aligned to containerPort from the
tree relationship alone); h02 by the frozen `runtime_constraints` primitive
(the `FailedScheduling` / `Insufficient memory` evidence mapped to the
Unschedulable signal, the absurd request clamped back to the chart floor).

## h03 Failure Analysis

Factually:

- No frozen primitive has a dedicated repair branch for the public
  Service-port contract (`service.port` vs the benchmark's published
  `SVC_PORT`). The frozen `service_wiring` primitive reasons only about
  targetPort↔containerPort — which was intact in h03.
- The frozen agent therefore **produced no change** (`repair_mode: no_change`,
  0 derived rounds, no fallback).
- The benchmark scored the unrepaired candidate correctly: **65** (backbone
  healthy; `http_health_ok` and `service_ports_wired` clause A FAIL, the
  latter naming the missing published port 80).
- The `service_ports_intact` anti-cheat correctly flagged the unrepaired
  candidate (`service.port must remain the published service port 80`).
- **No post-result tuning occurred** — the failure is preserved as-is. In
  principle the actionable signal exists (the failed `http_health_ok` reason
  names the missing port 80), so a more general evidence-driven agent could
  derive this repair; the frozen one cannot, and that is the finding.

## Evidence / Reproducibility

- Immutable artifact:
  [`docs/evidence/generalization-first-shot.json`](evidence/generalization-first-shot.json)
- Artifact SHA256:
  `3cd075bb544c499523fa11aa7694f22aedbf9ca5fbf413ef131093e59ec553cf`
- Agent freeze commit: `8ccbe0d62df1c336e2384d45486db52194630892`
- Held-out benchmark commit: `bd7fa2938d70c998268137dc136441302cd6028b`
- The artifact records the freeze commit, per-scenario SHA256 of
  `scenario.yaml` / `break.patch` / `golden.patch`, first/final scores,
  per-round provenance and the aggregate. Its hash was recomputed after the
  post-archive golden validation and was **byte-identical** — the archived
  first-shot result was frozen before any golden run and never regenerated.
- Rerun (unofficial, for reproduction only): `make generalization` or
  `python -m harness agent-generalization [--only h01,h03]`; the post-archive
  golden check is `--golden-check`.

## Limitations

- **Only 3 held-out scenarios** — far too few for any statistical claim of
  broad, universal generalization. This is a controlled probe, not a survey.
- **2 of 3 are Type A** relationship instances; the frozen primitives were
  built around those relationship categories, so 2/2 on Type A shows the
  relationships were implemented generally — not that the agent handles
  arbitrary unseen faults.
- **Only 1 scenario is genuinely novel (Type B), and the agent failed it**:
  h03 exposes a real capability boundary — the frozen primitive set covers a
  fixed (if reusable) family of relationships, and a fault outside that family
  produces an honest `no_change`, by design never silently rescued by a golden
  replay.
- The single-node kind environment makes h02's scheduling failure trivially
  reproducible; a multi-node cluster would need a re-derived precondition.
