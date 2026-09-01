# PipelineFixRL

Reproducible, local, **kind**-based CI/CD and Kubernetes repair environment for
the micro1 Agentic Workflows Hackathon.

- Spec: [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md)
- Plan + acceptance criteria: [`docs/PLAN.md`](docs/PLAN.md)

## Status

- **Milestone 1** (healthy base deploy end to end) — implemented; evidence in
  [`docs/M1_EVIDENCE.md`](docs/M1_EVIDENCE.md).
- **Milestone 2** (scenario-001, incorrect readiness probe path) — implemented;
  evidence in [`docs/M2_EVIDENCE.md`](docs/M2_EVIDENCE.md).
- **M-BE** (base-evolution prerequisites for scenarios 002–010) — implemented.
- **Milestones M3–M11 — all 10 scenarios implemented**; per-milestone evidence
  in [`docs/PLAN.md`](docs/PLAN.md) §11.8:
  - **M3** scenario-002 — wrong pinned image tag (`ErrImageNeverPull`)
  - **M4** scenario-003 — OOMKilled crash loop
  - **M5** scenario-004 — Helm value not wired to template (`InvalidImageName`)
  - **M6** scenario-005 — Service selects no pods
  - **M7** scenario-006 — container runs as root
  - **M8** scenario-007 — misconfigured ConfigMap reference (`CreateContainerConfigError`)
  - **M9** scenario-008 — structured-log format regression (`structured_logs_ok`)
  - **M10** scenario-009 — CI + health-contract regression (`ci_gate_pass` runs the real `scripts/ci.sh`)
  - **M11** scenario-010 — unresolved merge conflict (`image_build_ok`, `git_tree_resolved`)
  - M3–M8 are on `master` (submission commit `fc8f7e8`); **M9–M11 are on branch
    `continued-development`**.
- **Repair agents** — `baseline` (offline no-LLM heuristic, 7/10) and `advanced`
  (deriving fixer, **10/10 derived**, golden replay only as a visible fallback);
  see "Baseline & advanced solutions". On `continued-development`.

## Prerequisites

Docker Desktop **running**, plus `kind`, `helm`, `kubectl`, `make`, Python 3.11+.
On this host the first four came from winget:

```
winget install Kubernetes.kind Helm.Helm ezwinports.make
```

`scripts/lib.sh` adds the winget package dirs to `PATH` for shells opened before
the install, so `make` works without restarting the terminal.

## Quickstart (Milestone 1)

```bash
make setup       # create .venv, install app + harness
make doctor      # verify tools + pinned kind digest
make kind-up     # create the pinned 1-node kind cluster -> .state/kubeconfig
make test        # pytest
make e2e-base    # doctor -> kind-up -> test -> deploy-base -> verify-base -> clean-ns
make kind-down   # delete the cluster, clean .state/
```

## Quickstart (Milestone 2 — scenario-001)

```bash
make kind-up
make scenario-001-broken    # deploy base + break.patch; must FAIL readiness, score <= 60
make scenario-001-golden    # deploy base + break.patch + golden.patch; must score 100
make scenario-001-compose   # prove break.patch + golden.patch == base (byte-identical)
make scenario-001           # all three of the above in order
# or, self-contained:
make e2e-scenario-001       # doctor -> kind-up -> scenario-001
make kind-down
```

## Baseline & advanced solutions

Two repair-agent tiers (`harness/agents/fix_agent.py`) run against the **same**
environment and are scored by the **same** deterministic pipeline as `broken` /
`golden`. Each is handed the broken scenario tree, submits a candidate fix as a
unified diff, and the unchanged harness builds / deploys / scores it as a
`baseline` / `advanced` variant run.

### Architecture

| Tier | Inputs it may use | How it repairs |
|---|---|---|
| **baseline** | the broken scenario tree only | offline, deterministic, **no-LLM heuristic**. A small literal-substitution table (probe path, image override, memory floor, ConfigMap key, log format, `/health` body) plus a mechanical merge-conflict resolver. No route parsing, no runtime evidence, no cross-file correlation — so 004 / 005 / 006 are out of reach, a deliberate, visible capability boundary. |
| **advanced** | the broken tree's **own source files** + the **collected runtime evidence** of a broken run (`events` / `pods.json` / `logs/` / `build.log` / `ci.log`) + validation feedback from its own attempt | a **deriving fixer**. `_derive_repair` runs 10 fault-class detectors — probe path vs the routes the app actually serves, an image override that isn't on the node, OOMKilled + a memory floor, an undefined `.Values.image.*` key in the template, a hard-coded Service selector that diverged from the shared helper, an un-hardened `securityContext`, a `configMapKeyRef` key the ConfigMap doesn't define, a non-structured `logFormat`, a `/health` body that disagrees with the health test's asserted contract, and unresolved conflict markers — and constructs the repair from that evidence. It **never reads `golden.patch`, the golden variant, or any expected-repaired-file content** on the derivation path. |

### Derived vs. fallback (advanced)

Execution order is strict: **derive → run as `advanced` → validate (SCORE 100 +
anti-cheat clean) → only if that fails, explicitly replay `golden.patch` as a
fallback**. Every advanced result records provenance to
`advanced_provenance.json` and the eval matrix:

`repair_mode` (`derived` | `golden_fallback` | `no_change` | `failed`),
`derived_attempted`, `derived_validation_passed`, `fallback_used`,
`final_score`, `files_modified`.

A `golden_fallback` SCORE 100 is **not** a derived-agent success and is reported
distinctly everywhere.

### Commands

```bash
make kind-up

make baseline AGENT_SID=scenario-001      # heuristic fix
make advanced AGENT_SID=scenario-005      # derived fix
make agents-matrix                        # broken/golden/baseline/advanced x 001..010 -> results table + .state/agents/matrix.json

# or drive the harness directly:
.venv/Scripts/python -m harness agent --id scenario-004 --tier advanced   # prints its provenance
.venv/Scripts/python -m harness agent-matrix

make kind-down
```

### Results matrix (scenario-001 … scenario-010)

| scenario | broken | golden | baseline | baseline mode | advanced | advanced repair_mode |
|---|---:|---:|---:|---|---:|---|
| scenario-001 | 10 | 100 | 100 | heuristic | 100 | **derived** |
| scenario-002 | 10 | 100 | 100 | heuristic | 100 | **derived** |
| scenario-003 | 10 | 100 | 100 | heuristic | 100 | **derived** |
| scenario-004 | 10 | 100 | 10 | no_change | 100 | **derived** |
| scenario-005 | 50 | 100 | 50 | no_change | 100 | **derived** |
| scenario-006 | 55 | 100 | 55 | no_change | 100 | **derived** |
| scenario-007 | 10 | 100 | 100 | heuristic | 100 | **derived** |
| scenario-008 | 65 | 100 | 100 | heuristic | 100 | **derived** |
| scenario-009 | 50 | 100 | 100 | heuristic | 100 | **derived** |
| scenario-010 | 0 | 100 | 100 | heuristic | 100 | **derived** |

**advanced: 10 / 10 `derived`, 0 `golden_fallback`** — every fix constructed from
scenario-visible evidence, SCORE 100, anti-cheat clean. Verified deterministic
across two independent full runs, and by a runtime boundary probe: advanced still
produces `repair_mode: derived` with `golden.patch` physically removed from disk.
**baseline: 7 / 10** — `scenario-004` / `005` / `006` need multi-line template /
block reconstruction the heuristic does not attempt (their `baseline` score
equals the `broken` score).

### Limitations / remaining failure modes

- Advanced's detectors are hand-written per fault-class; a genuinely novel fault
  outside the ten classes would fall through to `golden_fallback` (visible in the
  provenance), not a derived fix.
- The derived security-context and image-ref repairs apply a *standard*
  hardened / canonical form; they happen to coincide with `golden` but are built
  from Kubernetes/Helm knowledge, not copied.
- Advanced needs one broken run's collected evidence; `agent-matrix` produces it,
  a standalone `harness agent --tier advanced` re-uses the last broken run or
  triggers one.

## Evaluation

```bash
make e2e-base                              # A1-A14 base-pipeline acceptance (SCORE 100)
make scenario-00N                          # broken + golden + compose-check, scenario N (1..10)
make agents-matrix                         # full 001..010 broken/golden/baseline/advanced matrix
```

**Methodology.** Every scenario has a one-line injected fault, a byte-exact
reference `golden.patch`, a weighted deterministic rubric, a scenario-specific
anti-cheat gate, and a `compose-check` proving `break + golden == base`. Agent
tiers are scored by the identical pipeline, so `broken` / `golden` / `baseline` /
`advanced` are directly comparable. Each scenario milestone was gated on a full
regression of every prior scenario before commit; the agent work was gated on
`make e2e-base` + scenario-001…010 broken/golden/compose + baseline + advanced.
Per-milestone evidence is in [`docs/PLAN.md`](docs/PLAN.md) §11.8.

## Improvement changelog

Iterative build — each scenario milestone was gated on a full regression of every
prior scenario **before** commit (evidence: `docs/PLAN.md` §11.8):

| Milestone | Commits | Change | Validation |
|---|---|---|---|
| M0–M1 | `67c767f` `e112ede` | base FastAPI app + Helm chart + kind lifecycle + deterministic harness | A1–A14 cold `make e2e-base`, SCORE 100 |
| M2 | `62b74ad` | scenario-001 — readiness probe path `/health`→`/health2` | broken ≤60 / golden 100 / compose |
| M-BE | `7f8a081` `e45b97a` | base-evolution prereqs for 002–010; universal image-hygiene anti-cheat | A1–A14 + scenario-001 |
| M3 | `1e58242` `18ffc15` | scenario-002 — wrong pinned tag (`ErrImageNeverPull`) | broken ×2 =10 / golden 100 / compose; +A1–A14, s001 |
| M4 | `bec74c9` `fcce116` | scenario-003 — OOMKilled crash loop; `no_oomkill` check | broken ×2 =10 / golden 100 / compose; +s001–002 |
| M5 | `57a853c` `0925933` | scenario-004 — Helm value not wired (`InvalidImageName`) | broken ×2 =10 / golden 100 / compose; +s001–003 |
| M6 | `ee847c3` `22eb6c1` | scenario-005 — Service selects no pods; `service_selects_pods` | broken ×2 =50 / golden 100 / compose; +s001–004 |
| M7 | `9492bdc` `4c7a559` | scenario-006 — container runs as root; 4 posture checks + `security_posture_intact` anti-cheat | broken ×2 =55 / golden 100 / compose; +A1–A14, s001–005 |
| M8 | `7393766` `68269af` | scenario-007 — misconfigured ConfigMap ref (`CreateContainerConfigError`); `config_applied` check + `config_wiring_intact` anti-cheat | broken ×2 =10 / golden 100 / compose; +A1–A14, s001–006 |
| README | `a8dd0f2` | status through M8 | — |
| agents (v1) | `af3414a` | first baseline + advanced tiers; `harness agent` CLI + `make baseline`/`advanced`/`eval-agents`; advanced = golden replay | baseline s001/s007 =100; advanced 001–007 =100 |
| **M9** | `929ad58` `98b8440` | scenario-008 — structured-log format regression; `structured_logs_ok` check (+ machine-derived stdout line-count floor) + `structured_logging_intact` anti-cheat | broken ×2 =65 / golden 100 / compose; +A1–A14, s001–008 |
| **M10** | `4ebb9c8` `c2c9915` | scenario-009 — CI + health contract; `ci_gate_pass` runs the real `scripts/ci.sh` from the ephemeral tree; `_TREE_PATHS` += `scripts/`+`config/` + `_assert_frozen_subtrees` guard + `ci_contract_intact` anti-cheat | broken ×2 =50 / golden 100 / compose; +A1–A14, s001–009; integrity probes 1–6 |
| **M11** | `eda6087` `af83069` | scenario-010 — unresolved merge conflict; `image_build_ok` + `git_tree_resolved` checks; `_finish_build_failure` completed per §11.4 + `merge_resolved_cleanly` anti-cheat | broken ×2 =0 / golden 100 / compose; +A1–A14, s001–010 |
| agents (v2) | `4a4bc3e` | advanced becomes a **deriving fixer** (10 fault-class detectors, scenario-visible evidence only, golden replay only as explicit provenance-recorded fallback); baseline broadened to 7/10; `harness agent-matrix` + `make agents-matrix` | matrix: **advanced 10/10 derived**, baseline 7/10; static + runtime golden-boundary probes; `make e2e-base` + s001–010 broken/golden/compose + baseline + advanced all green |

All work M9→agents-v2 is on branch `continued-development`; `master` stays at the
hackathon submission commit `fc8f7e8`.

## Intended user, bottleneck, value, failure mode

- **Intended user.** Teams building or benchmarking coding / SRE agents who need a
  *deterministic, offline, reproducible* target for "here is a broken Kubernetes
  deploy — fix it": no cloud, no LLM-as-judge, runs on a laptop.
- **Bottleneck it removes.** Evaluating repair agents today means hand-built
  broken clusters and subjective "looks fixed" grading. Here each scenario is a
  one-line fault with a byte-exact reference fix, a weighted deterministic
  rubric, a scenario-specific anti-cheat gate, and a compose-check proving
  `break + golden == base`.
- **Value.** A stable, regression-testable score for an agent across probes,
  image distribution, runtime resources, Helm templating, Services, Pod
  Security, and ConfigMaps — plus `broken` / `golden` / `baseline` / `advanced`
  runs that diff directly.
- **Main failure mode.** Needs a running Docker daemon + kind; a cold Docker
  cache makes runs minutes-long, and a fault that doesn't reproduce
  deterministically on a given kernel / CRI would score inconsistently
  (mitigated by the pinned node image, a per-scenario `deploy_timeout`, and the
  broken-run ×2 determinism gate).
- **Hot take.** Most "agent fixes Kubernetes" demos are unfalsifiable. Making the
  fix a diff and the grade a number is the boring part nobody does — and it's the
  only part that lets you tell a real improvement from a lucky prompt.

## Scenario anatomy

Each scenario lives in `harness/scenarios/<id>/` as `scenario.yaml` (definition +
per-variant expectations), `task.md` (agent-facing), `break.patch`, and
`golden.patch`. The runner copies the base tree, applies the patch(es), builds a
uniquely tagged image, deploys into a per-variant namespace, scores it with the
same deterministic checks as the base app, then compares the result to the
scenario's expectation block. A diff-based anti-cheat check rejects removed
probes, weakened `securityContext`, edited tests, or `replicaCount: 0`.

## Guarantees

- kind from milestone 1; `kind` version and `kindest/node` image pinned by
  version **and** SHA256 in `config/versions.env`.
- No cloud anything. Project-only kubeconfig at `.state/kubeconfig`; the user's
  `~/.kube/config` is never touched.
- Unique, non-`latest` image tags; `imagePullPolicy: Never`; images side-loaded
  with `kind load docker-image`.
- Every scenario is namespace-scoped; namespace deleted after each run, cluster
  after the suite.
- All scoring is deterministic (`harness/evaluate.py`). No LLM judging. All
  transient state lives under `.state/`.
