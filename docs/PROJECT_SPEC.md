# PipelineFixRL — Project Specification

Status: DRAFT (awaiting approval)
Last updated: 2026-08-28
Owner: changjae.lee0819@gmail.com
Event: micro1 Agentic Workflows Hackathon

---

## 1. Purpose

PipelineFixRL is a **reproducible, local, kind-based CI/CD and Kubernetes repair
environment**. It presents an agent with a broken Kubernetes deployment scenario,
lets the agent attempt a fix, and scores the result with **deterministic checks**
— no LLM judging.

The environment is used to compare four things per scenario:

| Actor            | Meaning                                                        |
|------------------|--------------------------------------------------------------- |
| Broken scenario  | Base app + `break.patch` applied. Expected to fail evaluation. |
| Golden solution  | Base app + `golden.patch` applied. Expected to score 100.      |
| Baseline agent   | A minimal agent's attempt (added in a later milestone).        |
| Advanced agent   | A stronger agent's attempt (added in a later milestone).       |

This spec covers **Milestone 1** (healthy base deployment end to end) and
**Milestone 2** (`scenario-001`, incorrect readiness probe path). The remaining
nine scenarios are explicitly **out of scope** until M1 and M2 both pass end to
end.

---

## 2. Hard constraints (safety & reproducibility)

These are non-negotiable and must be enforced in code and reviewed before merge.

### 2.1 kind is mandatory from Milestone 1
kind is used from the first milestone. It is **not** optional or a stretch goal.
Every deploy/evaluate path runs against a kind cluster.

### 2.2 No cloud, ever
- Never use real AWS, EKS, ECR, IAM credentials, or any cloud resource.
- No code path may call a cloud API, read `~/.aws`, or require network auth to a
  cloud provider.
- Registry access is local only: images are transferred with
  `kind load docker-image`. No external registry push/pull.

### 2.3 Kubeconfig isolation
- The project uses a dedicated kubeconfig at **`.state/kubeconfig`** (repo-root
  relative).
- The user's default kubeconfig (`~/.kube/config`) is **never** read or written.
- All `kubectl`, `helm`, and `kind` invocations pass `--kubeconfig
  "$REPO_ROOT/.state/kubeconfig"` (or `KUBECONFIG` scoped to that path only).
- `kind create cluster` is always called with `--kubeconfig` pointing at the
  project path.

### 2.4 Cluster shape
- One control-plane kind node for the MVP. No workers.
- kind version and `kindest/node` image are **pinned by version and SHA256
  digest** (see §6). `latest` is forbidden for the node image.

### 2.5 Image hygiene
- Application image tag is **never** `latest`.
- Every build produces a **unique tag**: `pipelinefixrl/app:<scenario>-<shortsha>-<timestamp>`
  or `pipelinefixrl/app:<uuid>`. The exact scheme is fixed in PLAN §Image tagging.
- `imagePullPolicy` is `IfNotPresent` or `Never` (chart default: `Never`, since
  the image is always side-loaded into kind).

### 2.6 Namespace scoping
- Each scenario runs in its **own namespace**: `pfrl-<scenario>-<run-id>`.
- No scenario touches `default`, `kube-system`, or another scenario's namespace.
- Cleanup deletes the namespace after each run; the cluster is deleted after the
  full evaluation suite.

### 2.7 Evaluation integrity
- Results are **never fabricated**. Every score is computed from real cluster
  state captured in that run.
- No LLM-based judging. All scoring is deterministic and reproducible.
- Tests, probes, and security controls are **never weakened, deleted, or
  bypassed** to make a scenario pass. An agent solution that does so is scored as
  a failure by an explicit anti-cheat check (M2+).

### 2.8 State containment
- All transient state — kubeconfig, cluster metadata, per-run workspaces,
  captured artifacts — lives under **`.state/`**.
- `.state/` is git-ignored. Nothing under `.state/` is required to rebuild the
  environment from scratch.

---

## 3. Technology (MVP)

| Concern            | Choice                                                    |
|--------------------|--------------------------------------------------------- -|
| Language           | Python 3 (3.11+; dev machine has 3.14)                    |
| API framework      | FastAPI + Uvicorn                                         |
| Tests              | pytest                                                    |
| Container build    | Docker                                                    |
| Local cluster      | kind (pinned)                                             |
| Cluster CLI        | kubectl (pinned minor via node image)                     |
| Packaging / deploy | Helm 3                                                    |
| Task runner        | GNU Make (`Makefile` is the canonical interface)          |
| Agent              | Claude Code                                               |

### 3.1 Host prerequisites (developer machine)

Must be installed and on `PATH` before `make setup` can succeed:

- Docker (running daemon) — **currently installed but not running on this host**
- kind — **not yet installed**
- helm — **not yet installed**
- make — **not yet installed**
- kubectl — installed
- Python 3.11+ — installed

`make doctor` verifies all of the above and prints versions + the pinned digest
match. See PLAN for the install commands chosen for this Windows host.

---

## 4. Repository layout (target)

```
micro/
├── .gitignore
├── .state/                     # git-ignored; all runtime state
│   ├── kubeconfig              # project-only kubeconfig
│   ├── clusters/               # kind cluster metadata
│   └── runs/<run-id>/          # per-run captured artifacts (events, logs, json)
├── Makefile                    # canonical entrypoint for every workflow
├── README.md
├── docs/
│   ├── PROJECT_SPEC.md         # this file
│   └── PLAN.md
├── config/
│   └── versions.env            # pinned versions + SHA256 digests (single source)
├── pyproject.toml              # app + harness deps, pytest config
├── app/
│   ├── __init__.py
│   └── main.py                 # FastAPI: GET / and GET /health
├── tests/
│   └── test_health.py          # pytest unit tests for the app
├── docker/
│   └── Dockerfile              # multi-stage, non-root, distroless-ish runtime
├── charts/
│   └── app/                    # Helm chart for the application
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── _helpers.tpl
│           ├── deployment.yaml
│           └── service.yaml
├── scripts/
│   ├── lib.sh                  # shared bash helpers (paths, kubeconfig, logging)
│   ├── kind-up.sh             # create pinned cluster -> .state/kubeconfig
│   ├── kind-down.sh          # delete cluster + clean .state/clusters
│   └── kind-cluster.yaml       # kind config: 1 control-plane, pinned node image
└── harness/
    ├── __init__.py
    ├── cli.py                  # `python -m harness ...` entrypoint
    ├── run.py                  # orchestrates: ns -> build -> load -> helm -> collect -> evaluate -> cleanup
    ├── collect.py              # gather events, logs, rollout, readiness, svc, endpoints
    ├── evaluate.py             # deterministic checks -> score
    ├── report.py               # render run report (json + text) under .state/runs/
    ├── api.py                  # FastAPI control plane (thin; wraps run.py)
    └── scenarios/
        └── scenario-001/
            ├── scenario.yaml   # declarative scenario definition
            ├── task.md         # agent-facing problem statement
            ├── break.patch     # applied to produce the broken variant
            └── golden.patch    # applied to produce the golden variant
```

(Exact contents defined in PLAN. Anything not listed here is out of scope for
M1/M2.)

---

## 5. Core workflow (the 9 steps)

The harness implements this pipeline. Each step writes an artifact under
`.state/runs/<run-id>/`.

1. **Cluster** — ensure a clean pinned kind cluster exists; kubeconfig at
   `.state/kubeconfig`.
2. **Namespace** — create `pfrl-<scenario>-<run-id>`; label it with scenario +
   run metadata.
3. **Build** — `docker build` the app image with a unique tag.
4. **Load** — `kind load docker-image <tag> --name <cluster>`.
5. **Deploy** — `helm upgrade --install` the chart into the namespace with the
   unique tag and `imagePullPolicy: Never`.
6. **Collect** — capture, as files:
   - `kubectl get events` (namespace, sorted by lastTimestamp)
   - `kubectl logs` for every pod / container (current + previous if restarted)
   - `kubectl rollout status deploy/<name>` outcome + `.status` JSON
   - readiness: pod `.status.conditions`, `readyReplicas`, container `ready`
   - `kubectl get svc -o json`
   - `kubectl get endpoints -o json` (and `endpointslices`)
7. **Evaluate** — run deterministic checks (§7) against the collected state;
   produce a score 0–100 and a per-check pass/fail table.
8. **Compare** — place the run under a scenario+variant key so
   broken / golden / baseline / advanced runs can be diffed. (M2: broken vs
   golden only.)
9. **Cleanup** — delete the namespace after the run; delete the cluster after the
   evaluation suite (`make eval` teardown, or `make kind-down`).

---

## 6. Version pinning (§2.4 detail)

`config/versions.env` is the **single source of truth**. Every script sources it.

```
# EXAMPLE — exact values to be fixed during implementation from the official
# kind release notes for the chosen version. Digest MUST be copied verbatim from
# https://github.com/kubernetes-sigs/kind/releases and never invented.
KIND_VERSION=v0.27.0
KIND_NODE_IMAGE=kindest/node:v1.32.2@sha256:<COPY_FROM_RELEASE_NOTES>
KIND_CLUSTER_NAME=pipelinefixrl
HELM_MIN_VERSION=3.14.0
PYTHON_MIN_VERSION=3.11
APP_IMAGE_REPO=pipelinefixrl/app
```

Rules:
- `kind-cluster.yaml` references `KIND_NODE_IMAGE` including the `@sha256:` digest.
- `make doctor` fails if the running `kind version` != `KIND_VERSION`.
- `make doctor` fails if the node image digest in `kind-cluster.yaml` != the one
  in `versions.env`.
- The digest is verified against the release notes at implementation time and
  recorded in the PR description.

---

## 7. Deterministic evaluation

### 7.1 Checks (M1 + M2)

Each check is a pure function of collected artifacts → `PASS | FAIL | NA` plus a
short reason string. Weights sum to 100.

| ID                  | Weight | Pass condition                                                                 |
|---------------------|-------:|-------------------------------------------------------------------------------- |
| `helm_release_ok`   |     10 | `helm status` = `deployed`; no failed hooks.                                    |
| `rollout_complete`  |     20 | `kubectl rollout status` returns success within `deploy_timeout` (default 120s).|
| `deployment_ready`  |     20 | `.status.readyReplicas == .spec.replicas` and `updatedReplicas` matches.       |
| `pods_ready`        |     15 | Every pod has `Ready=True`; total container restarts ≤ `restart_threshold` (0).|
| `endpoints_present` |     15 | The Service's Endpoints/EndpointSlice has ≥1 ready address on the target port. |
| `http_health_ok`    |     20 | `GET /health` via in-cluster probe returns HTTP 200 and body `{"status":"ok"}`.|
| `no_bad_events`     |      0 | (report-only in M1/M2) count of `Warning` events of type `Unhealthy`, `BackOff`, `FailedScheduling`. |

- **Score** = sum of weights of `PASS` checks, ignoring `NA`, normalized to the
  sum of weights of non-`NA` checks, ×100, rounded to integer.
- **Pass threshold** for "healthy": `score == 100` **and** zero `FAIL`.
- `http_health_ok` probe method: `kubectl run` a short-lived `curl`/`wget` pod in
  the namespace, or `kubectl exec` into the app pod, or `kubectl port-forward`
  from the harness host. Method fixed in PLAN; must not depend on Ingress or
  LoadBalancer.

### 7.2 Anti-cheat (M2+, defined now)

A scenario solution is scored **0 / FAIL** regardless of check results if any of:
- A liveness or readiness probe present in the base chart was removed.
- `securityContext` fields present in the base chart (`runAsNonRoot`,
  `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`) were removed or
  weakened.
- A test file under `tests/` was modified.
- Replica count was set to 0.

Anti-cheat is a diff-based check between the submitted tree and the base tree.
For M2 the golden patch is verified to not trip anti-cheat.

### 7.3 Scenario definition schema (`scenario.yaml`)

```yaml
id: scenario-001
title: Incorrect readiness probe path
category: kubernetes/probes
difficulty: intro
description: >
  The readiness probe points at a path the app does not serve, so pods never
  become Ready and the Service has no endpoints.
target:
  chart: charts/app
  namespace_prefix: pfrl-scenario-001
patches:
  break: break.patch
  golden: golden.patch
evaluation:
  deploy_timeout_seconds: 120
  restart_threshold: 0
  expect:
    broken:
      score_max: 60            # broken variant must score at or below this
      must_fail: [rollout_complete, deployment_ready, endpoints_present, http_health_ok]
    golden:
      score_min: 100           # golden must score exactly this
      must_pass: [helm_release_ok, rollout_complete, deployment_ready, pods_ready, endpoints_present, http_health_ok]
```

---

## 8. The base application

### 8.1 Behavior
- `GET /` → `200`, JSON `{"name": "pipelinefixrl-app", "version": "<from env or package>"}`.
- `GET /health` → `200`, JSON `{"status": "ok"}`. No external dependencies; always
  fast. This is the readiness AND liveness target.
- Listens on `0.0.0.0:8000` (configurable via `APP_PORT`).
- Structured single-line logs to stdout.

### 8.2 Container
- Multi-stage Dockerfile. Final image runs as non-root (`runAsNonRoot` compatible,
  UID 1000+), no shell package manager in the runtime layer if practical.
- `EXPOSE 8000`. `CMD` runs uvicorn with 1 worker.
- Healthcheck is defined by the Kubernetes probes, not `HEALTHCHECK` in the image
  (keeps probe logic in the chart where scenarios patch it).

### 8.3 Chart defaults (`charts/app/values.yaml`)
- `replicaCount: 1`
- `image.repository`, `image.tag` (required, no default `latest`), `image.pullPolicy: Never`
- `service.type: ClusterIP`, `service.port: 80`, `service.targetPort: 8000`
- `readinessProbe.httpGet.path: /health`, port `8000`, `initialDelaySeconds: 2`,
  `periodSeconds: 5`, `failureThreshold: 3`
- `livenessProbe.httpGet.path: /health`, port `8000`, `initialDelaySeconds: 5`,
  `periodSeconds: 10`, `failureThreshold: 3`
- `resources`: small requests/limits set
- `securityContext`: `runAsNonRoot: true`, `runAsUser: 1000`,
  `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`,
  `capabilities.drop: [ALL]`
- `podSecurityContext`: `seccompProfile.type: RuntimeDefault`

---

## 9. Scenario-001 (Milestone 2)

- **Fault**: `break.patch` changes `charts/app/values.yaml`
  `readinessProbe.httpGet.path` from `/health` to `/healthz` (a path the app does
  not serve → 404 → readiness never succeeds → pod never Ready → Service has no
  endpoints → `http_health_ok` unreachable via the Service).
- **Golden**: `golden.patch` sets the path back to `/health`. Nothing else
  changes. Does not trip anti-cheat.
- **Expected broken result**: `rollout_complete=FAIL` (times out),
  `deployment_ready=FAIL`, `pods_ready=FAIL`, `endpoints_present=FAIL`,
  `http_health_ok=FAIL`; `helm_release_ok=PASS`. Score ≤ 60.
- **Expected golden result**: all weighted checks `PASS`, score = 100.

---

## 10. Out of scope for M1 + M2

- Scenarios 002–010.
- Baseline agent and advanced agent implementations (schema reserved, not built).
- Multi-node clusters, Ingress, service mesh, network policies.
- CI running on GitHub Actions (local `make` only for now).
- Any registry other than kind's built-in image store.
- Web UI beyond the FastAPI JSON control plane.

---

## 11. Acceptance criteria

See PLAN §"Acceptance criteria" for the exact, command-level checks that define
"done" for Milestone 1 and Milestone 2.
