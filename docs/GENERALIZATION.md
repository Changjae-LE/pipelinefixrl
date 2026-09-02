# Generalization Evaluation

Two independent held-out evaluations have been run, each against its own
frozen agent and its own benchmark. **v1 is preserved verbatim below; v2 is a
separate section. The two results are never merged, and v2 never revises v1.**

---

# v1 Generalization Evaluation

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

---

# v2 Generalization Evaluation

## V2 Motivation

v1's single Type B case, h03, failed: the frozen v1 agent had no way to use an
expectation that lives in **consumer evidence** rather than in the tree. Its
`Evidence` layer already captured the signal (a failed check whose reason named
the port a consumer tried to reach), but no primitive consumed it.

v2 closes that gap generically. `harness/agents/contracts.py` adds typed
`Declaration` / `Expectation` / `Reconciliation` records: parsers extract facts
from a run's own evidence using documented Kubernetes/kubectl error formats, and
a single deterministic `reconcile()` decides repairs. Ambiguous, insufficient or
conflicting evidence, duplicate evidence, unresolved resource identity, and a
current value with independent tree-side corroboration all mean **no change**.

Consequently **h03 became a known development/regression case in v2** — it is
repaired by the general consumer-contract relationship, with no scenario id,
path match, or hard-coded value anywhere in agent code. **h03 was not reused as
v2 held-out evidence, and v1's official 2/3 record is unchanged.** The v2
development/regression matrix is **13/13 derived** (scenario-001…010 +
h01/h02/h03); that is regression performance on faults the agent was developed
against and is **not** generalization evidence.

## V2 Freeze

| | commit |
|---|---|
| **V2 agent freeze** | `4d538b74f31e1a13291a61e6cee5744c516183c9` |
| **Locked benchmark** | `ea980e319ed3b4ab3dc48934d7406cdc4da8e474` |
| **Official evidence** | `cf03bcd9f2d66d45183ff0254f65718524c33e9c` |

All eight held-out scenarios were authored **after** the freeze commit.
`harness/agents/**` remained **byte-identical** to `4d538b7` throughout held-out
authoring, broken-contract validation, the official first-shot, and post-archive
golden validation — verified with `git diff 4d538b7… -- harness/agents` at every
gate, and enforced continuously by a fast test. No held-out result was used to
tune the agent. The 32 scenario files were hash-locked and re-verified at each
gate.

## V2 Held-Out Taxonomy

| scenario | class | fault relationship | first-shot | rounds | result |
|---|---|---|---|---|---|
| vh01 | **A** | liveness probe port vs containerPort (`http_contract.probe_port` — the only frozen diagnosis with no development instance) | **100** | 1 | `derived` |
| vh02 | **A** | image tag sourced from a defined-but-wrong `.Values.image.*` key (`chart_value_wiring.template_image_ref`) | **100** | 1 | `derived` |
| vh03 | **A** | `capabilities.drop` emptied alone (`runtime_constraints.security_context`, minimal delta) | **100** | 1 | `derived` |
| vh04 | **B** | the application binds a port the deployment contract does not expect | **10** | 0 | `no_change` |
| vh05 | **B** | pod-level seccomp baseline dropped | **85** | 0 | `no_change` |
| vh06 | **B** | workload scaled to zero (rollout still reports success) | **40** | 0 | `no_change` |
| vh07 | **Compound** | unserved readiness path **+** emptied capability set | **100** | 1 | `derived` |
| vh08 | **Compound** | build-blocking conflict hiding a published-port regression | **100** | 2 | `derived` |

**Type A** = a held-out *instance* of a relationship the frozen v2 agent
explicitly represents. **Type B** = a genuinely novel relationship with no
dedicated frozen repair branch. Classification was done by inspecting the frozen
implementation, not by predicting outcomes — which is how two earlier
provisional labels were corrected before authoring (a *named* `targetPort`
mismatch turned out to be Type B, not Type A, and the Service-selector repair
only triggers when the shared helper include is absent).

## V2 Official First-Shot Results

Executed once, fallback disabled, golden-access guard armed, archived before any
golden run.

- **overall = 5/8 derived**
- **Type A = 3/3**
- **Type B = 0/3**
- **Compound = 2/2**
- **golden_fallback = 0**
- **no_change = 3**
- **exceptions = 0**

vh04, vh05 and vh06 produced `no_change` with **zero derived rounds and no
primitive match at all**: no frozen primitive represented the required
relationship, so the agent submitted nothing rather than guessing. The frozen
agent has no reasoning about the port the application process actually binds, no
reasoning about the pod-level security context, and no reasoning about workload
replica capacity. These are not near-misses and they are not softened here:
**every genuinely novel relationship in this benchmark defeated the agent.**

## Composition / Iteration Findings

**vh07 repaired two independent findings in ONE round.** The composition engine
produced a single candidate carrying both `http_contract.probe_path` and
`runtime_constraints.security_context` edits — different regions of the same
file, no edit conflict. This is the first **production/runtime** evidence that
same-candidate composition works outside the fast tests.

**vh08 required TWO observed rounds.** Round 1 resolved the build-blocking
dependency conflict and scored **75**; only once the image built and the release
deployed did the published-port fault become observable as consumer evidence,
and round 2 repaired the `Service.port` contract to reach **100**. The archived
artifact preserves `first_derived_score = 75` so this is never presented as an
immediate solve. Two rounds were **observed**, not forced by benchmark design —
the scenario was authored on the hypothesis that the second fault would be
hidden behind the first, and the run is reported as it happened.

## Golden Validation

Run only **after** the official artifact was archived and hash-frozen:

- vh01–vh08 golden = **100 for all eight**
- every expectation **MATCH**
- anti-cheat **clean** on every scenario
- every `break + golden` composition **byte-identical** to base
- **no benchmark-methodology defect found**

This is what licenses the interpretation of the Type B results: all three faults
**are** repairable and their reference repairs score a clean 100, so vh04–vh06
measure genuine **capability boundaries of the frozen agent**, not invalid or
unreachable scenarios.

## Evidence

- Artifact: [`evidence/generalization-first-shot-v2.json`](evidence/generalization-first-shot-v2.json)
- SHA256: `aeb57f0d7aa459fc568f99cafd84d1c1d84db08573a934ae5d94abb74230c4c2`
- Generated **exactly once**; the writer **refuses overwrite**; the artifact was
  **byte-identical before and after** post-archive golden validation (verified
  with `sha256sum -c` at both points); the **official first-shot was never
  rerun** (confirmed by timestamp — the newest advanced run directory precedes
  the artifact's `generated_utc`).
- The artifact records the v2 freeze commit, the locked benchmark commit,
  per-scenario file hashes, per-round provenance, and `official_first_shot: true`.
- v1's artifact remains immutable at
  `3cd075bb544c499523fa11aa7694f22aedbf9ca5fbf413ef131093e59ec553cf`, pinned by
  a fast test.

Reproduction (unofficial): `python -m harness agent-generalization` drives the
suite; the official run additionally archived with `--archive`.

## Limitations

- **8 held-out scenarios is still a small benchmark.** It is a controlled probe,
  not a survey, and supports no statistical claim.
- **Only 3 of the 8 are genuinely novel (Type B) relationships.** Three are
  Type A instances and two are Compound cases built from represented
  relationships.
- **Type A success does not demonstrate unseen fault-class discovery.** It shows
  those specific relationships were implemented generally enough to survive a
  new instance — nothing more.
- **Type B = 0/3 is a clear capability boundary.** The primitive set covers a
  fixed, reusable family of relationships; anything outside it yields an honest
  `no_change`, never a silent golden rescue.
- **No universal generalization claim is made**, in v1 or v2. The trajectory
  (v1 2/3 → v2 5/8 on a larger, harder benchmark) reflects one closed capability
  gap, not general repair ability.
- Environment-dependent scenarios (h02's scheduling floor, vh01's liveness
  timing window) are tuned to a single-node kind cluster and would need
  re-derived preconditions elsewhere.
