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
- **Milestones M3–M8** — implemented; per-milestone evidence in
  [`docs/PLAN.md`](docs/PLAN.md) §11.8:
  - **M3** scenario-002 — wrong pinned image tag (`ErrImageNeverPull`)
  - **M4** scenario-003 — OOMKilled crash loop
  - **M5** scenario-004 — Helm value not wired to template (`InvalidImageName`)
  - **M6** scenario-005 — Service selects no pods
  - **M7** scenario-006 — container runs as root (`runs_as_nonroot`,
    `readonly_rootfs`, `no_priv_escalation`, `caps_dropped` checks)
  - **M8** scenario-007 — misconfigured ConfigMap reference
    (`CreateContainerConfigError`; `config_applied` check)
- **Baseline & advanced repair agents** — implemented (`harness/agents/fix_agent.py`,
  `make baseline` / `advanced` / `eval-agents`); see "Baseline & advanced solutions".
- Scenarios 008–010 (M9–M11) — not started.

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

Two repair-agent tiers run against the **same** environment and are scored by the
**same** deterministic pipeline as `broken` / `golden`:

| Tier | What it is | How it fixes |
|---|---|---|
| **baseline** | offline, no-LLM heuristic (`harness/agents/fix_agent.py`) | knows a few common single-token misconfigurations (probe-path typo, ConfigMap-key typo) and edits `charts/app/values.yaml`; submits **no change** for anything outside that table |
| **advanced** | the Claude Code agentic workflow | reads `task.md` + live `kubectl describe` / `get events` on the broken deployment, reasons across the Helm/Kubernetes object graph, edits the chart. It authored every scenario's reference fix and anti-cheat rules (see git history + `submission/agent-trajectory.jsonl`); this module replays its converged, anti-cheat-clean fix so the tier is runnable and scored |

```bash
make kind-up

make baseline AGENT_SID=scenario-001      # heuristic fix        -> SCORE 100
make advanced AGENT_SID=scenario-005      # workflow fix          -> SCORE 100
make baseline AGENT_SID=scenario-005      # no rule -> no change  -> broken-level score

make eval-agents AGENT_SID=scenario-007   # broken + golden + baseline + advanced, all scored

# or drive the harness directly:
.venv/Scripts/python -m harness agent --id scenario-001 --tier baseline
.venv/Scripts/python -m harness agent --id scenario-001 --tier advanced

make kind-down
```

Verified: `baseline` solves scenario-001 and scenario-007 (SCORE 100);
scenario-002/003/004/005/006 are outside the heuristic's reach — an intentionally
visible capability boundary (e.g. `baseline scenario-005` → SCORE 50).
`advanced` solves all seven (SCORE 100, anti-cheat clean).

## Evaluation

```bash
make e2e-base                              # A1-A14 base-pipeline acceptance (SCORE 100)
make scenario-00N                          # broken + golden + compose-check, scenario N (1..7)
make eval-agents AGENT_SID=scenario-00N    # + baseline + advanced scored runs
```

Per-milestone evidence (scores, determinism, regression, teardown) is recorded in
[`docs/PLAN.md`](docs/PLAN.md) §11.8. Full build trajectory:
`submission/agent-trajectory.jsonl`.

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
| agents | this commit | baseline + advanced repair-agent tiers; `harness agent` CLI + `make baseline` / `advanced` / `eval-agents` | baseline s001/s007 =100, s005 no-op=50; advanced s005 =100 |

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
