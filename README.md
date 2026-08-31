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
- Scenarios 008–010 (M9–M11) and the baseline/advanced agents — not started.

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
