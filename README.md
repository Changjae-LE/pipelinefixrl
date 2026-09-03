# PipelineFixRL

**Evidence-driven automated repair for CI/CD and Kubernetes failures.**

[![CI](https://github.com/Changjae-LE/pipelinefixrl/actions/workflows/ci.yml/badge.svg)](https://github.com/Changjae-LE/pipelinefixrl/actions/workflows/ci.yml)

PipelineFixRL diagnoses broken container and Kubernetes deployments from source files and runtime evidence, derives deterministic repair patches, and validates them in a real Docker + kind environment.

The runtime repair engine is implemented in **Python** and does **not** use Claude, GPT, or another LLM to generate repairs.

**Tech:** Python · Kubernetes · kind · Docker · Helm · FastAPI · pytest · GitHub Actions

---

## Key Results

| Evaluation | Result |
|---|---:|
| v2 development / regression | **13/13 derived** |
| v2 frozen held-out first-shot | **5/8 derived** |
| Type A — represented relationships | **3/3** |
| Type B — genuinely novel relationships | **0/3** |
| Compound failures | **2/2** |
| Post-archive golden validation | **8/8 at 100** |
| Golden fallback | **0** |

The v2 agent was **frozen before the held-out benchmark was authored**.

Type B failures were preserved without post-result tuning. Golden validation later reached 100 on all eight scenarios, confirming that those failures were capability boundaries of the frozen agent rather than invalid benchmark cases.

> **13/13 is regression performance, not held-out generalization.**

---

## How It Works

```text
Broken CI / Kubernetes deployment
               │
               ▼
        Collect evidence
 source · manifests · build output
 events · pods · logs · failed checks
               │
               ▼
      Relationship reasoning
               │
               ▼
       Generate candidate patch
               │
               ▼
       Build + deploy + score
               │
        ┌──────┴──────┐
        │             │
      healthy       unhealthy
        │             │
        ▼             ▼
      success    observe new evidence
                      │
                      └──► refine repair
```

A patch is not considered correct because it looks plausible. It must actually build, deploy, and satisfy the same deterministic checks used to score every other variant.

---

## Repair Architecture

The advanced repair engine uses the broken tree plus evidence from its own runtime attempts.

It contains **7 reusable relationship primitives** covering:

- source integrity and merge conflicts
- Helm value wiring and image configuration
- readiness/liveness and HTTP contracts
- Service selectors and ports
- runtime resource and security constraints
- ConfigMap/configuration contracts
- consumer-visible endpoint contracts

v2 added typed consumer-contract reasoning:

```text
runtime evidence ──► Expectation
tree/source state ─► Declaration

Expectation + Declaration
          │
          ▼
deterministic reconciliation
          │
          ▼
Finding ──► repair candidate
```

Ambiguous or conflicting evidence results in `no_change` rather than a guess.

The repair loop can refine a candidate for up to three rounds:

```text
derive → apply → validate → observe → refine
```

Full architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

---

## Example: Two-Round Recovery

Held-out scenario `vh08` demonstrates why iterative repair matters.

It contains two independent faults:

1. an unresolved merge conflict blocks the build;
2. a Service publishes the wrong port.

The second fault cannot produce useful runtime evidence until the first is fixed.

| Round | Observable problem | Repair | Score |
|---|---|---|---:|
| 1 | build blocked by merge conflict | resolve conflict | **75** |
| 2 | consumer expects missing Service port | reconcile Service contract | **100** |

The official evidence preserves:

```text
first_derived_score = 75
final_derived_score = 100
```

so the result is never presented as an immediate one-shot solve.

`vh07` also demonstrated deterministic multi-finding composition by repairing two independent faults in a single candidate.

---

## Why the Evaluation Is Credible

PipelineFixRL uses several controls to make repair results falsifiable.

- **Agent freeze** — the repair engine is frozen before held-out scenarios exist.
- **Held-out authoring after freeze** — benchmark cases are created afterward.
- **Golden-access guard** — the derivation path cannot read `golden.patch`.
- **Golden fallback disabled** — held-out success must be genuinely derived.
- **One official first-shot run** — results are archived exactly once.
- **Fail-closed anti-cheat** — repairs cannot pass by weakening probes, security posture, replica requirements, or benchmark contracts.
- **Compose-check** — broken + golden patches must cleanly reconstruct the base.
- **No post-result tuning** — Type B remained **0/3** after evaluation.

Full methodology: [`docs/GENERALIZATION.md`](docs/GENERALIZATION.md)

---

## Held-Out Result

The official v2 first-shot result was:

```text
Overall:          5/8 derived
Type A:           3/3
Type B:           0/3
Compound:         2/2
golden_fallback:  0
no_change:        3
exceptions:       0
```

**Type A** scenarios are new instances of relationships already represented by the frozen agent.

**Type B** scenarios require genuinely absent reasoning capabilities. All three produced `no_change` and were intentionally preserved as capability boundaries.

**Compound** scenarios test multi-fault composition and iterative evidence discovery.

Post-archive golden validation reached **8/8 at score 100** with all expectations matching and anti-cheat checks clean.

Official evidence:

- [v1 first-shot artifact](docs/evidence/generalization-first-shot.json)
- [v2 first-shot artifact](docs/evidence/generalization-first-shot-v2.json)

---

## Quick Start

### Prerequisites

- Docker Desktop
- Python 3.11+
- `kind`
- `helm`
- `kubectl`
- `make`

### Setup

```bash
make setup
make doctor
make quick
```

### Run a scenario

```bash
make kind-up
make scenario-001
make kind-down
```

### Run the advanced repair agent

```bash
make kind-up
make advanced AGENT_SID=scenario-005
make agents-matrix
make kind-down
```

### Fast validation

```bash
make test-fast
make lint-py
```

The current fast suite contains **337 deterministic tests**.

---

## Repository Structure

```text
app/                         FastAPI application
charts/app/                  Helm chart
docker/                      container build

harness/
├── agents/                  repair engine
├── checks/                  deterministic scoring
├── scenarios/               development + held-out benchmarks
├── anticheat.py             fail-closed integrity rules
├── generalization.py        frozen-agent evaluation protocol
└── scenario.py              scenario orchestration

tests/                       application tests
tests_meta/                  fast harness/agent tests
docs/                        architecture, methodology, evidence
.github/workflows/           CI + manual E2E
```

---

## Limitations

PipelineFixRL is intentionally bounded.

- The repair engine can only reason about relationships represented by its primitives.
- All three genuinely novel Type B relationships in v2 remained unsolved.
- Eight held-out scenarios are too small for a universal or statistical generalization claim.
- Some repair logic encodes explicit Kubernetes/Helm domain knowledge.
- Runtime evaluation requires Docker and a local kind cluster.
- This is a benchmark/reference repair system, not an autonomous production remediation platform.

No universal repair claim is made.

---

## Project Evolution

PipelineFixRL began as a Kubernetes repair environment for the **micro1 Agentic Workflows Hackathon** and was extended after the original submission.

| Tag | Snapshot |
|---|---|
| `submission-final` | original hackathon submission |
| `v1-final` | completed v1 + first held-out evaluation |
| `v2-final` | completed v2 evaluation/documentation snapshot |

```text
v1
development: 10/10 derived
held-out first-shot: 2/3

        ↓
missing consumer-contract capability identified
        ↓

v2
consumer-contract reasoning added
development/regression: 13/13
fresh held-out first-shot: 5/8
```

The original v1 result remains unchanged.

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Generalization methodology](docs/GENERALIZATION.md)
- [Project specification](docs/PROJECT_SPEC.md)
- [Milestone history](docs/PLAN.md)
- [v1 official evidence](docs/evidence/generalization-first-shot.json)
- [v2 official evidence](docs/evidence/generalization-first-shot-v2.json)
