# PipelineFixRL — Implementation Plan

Status: DRAFT (awaiting approval)
Last updated: 2026-08-28
Companion to: `docs/PROJECT_SPEC.md`

---

## 0. Current state of the repo

- `C:\micro` is empty and **not a git repository**.
- Host tooling: Docker installed (**daemon stopped**), kubectl present, Python
  3.14 present. **kind, helm, make not installed.** `winget` and `go` available.

## 1. Environment decisions for this Windows host

| Need   | Decision                                                                    |
|--------|--------------------------------------------------------------------------- -|
| git    | `git init` at repo root (step M0). Add `.gitignore` before first commit.     |
| make   | Install GNU Make via `winget install ezwinports.make` (or GnuWin32). The `Makefile` is the canonical interface; a thin `make.ps1` wrapper is added only if `winget` install proves unreliable. |
| kind   | `winget install Kubernetes.kind` **pinned**, then `make doctor` asserts the version equals `config/versions.env`. Fallback: `go install sigs.k8s.io/kind@<KIND_VERSION>`. |
| helm   | `winget install Helm.Helm`.                                                 |
| Docker | User starts Docker Desktop manually; `make doctor` checks `docker info` succeeds and fails fast with a clear message otherwise. |
| Shell  | Scripts are POSIX `sh`/`bash` run via Git Bash (already used by this session). `Makefile` recipes call `bash scripts/*.sh`. |

All of the above are confirmed by `make doctor` before any cluster action.

## 2. Pinned versions (to finalize at M0)

`config/versions.env` — exact values chosen from the official kind release notes
at implementation time. Digest copied verbatim from
`https://github.com/kubernetes-sigs/kind/releases`, **never fabricated**, and
recorded in the M0 commit message / PR body.

```
KIND_VERSION=v0.27.0                 # or newest stable; locked at M0
KIND_NODE_IMAGE=kindest/node:v1.32.2@sha256:<verified-at-M0>
KIND_CLUSTER_NAME=pipelinefixrl
HELM_MIN_VERSION=3.14.0
PYTHON_MIN_VERSION=3.11
APP_IMAGE_REPO=pipelinefixrl/app
DEPLOY_TIMEOUT_SECONDS=120
```

## 3. Image tagging scheme

`pipelinefixrl/app:<variant>-<gitsha12>-<utc-YYYYMMDDHHMMSS>`
- `<variant>` ∈ `base | scenario-001-broken | scenario-001-golden | <agent>`.
- If not in a git tree yet, `<gitsha12>` = `nogit`.
- Chart always deploys with `image.pullPolicy: Never`; image is always
  `kind load`-ed first. `latest` never appears.

## 4. `.state/` layout produced at runtime

```
.state/
├── kubeconfig                       # created by scripts/kind-up.sh, chmod 600
├── clusters/pipelinefixrl.json      # `kind get` metadata snapshot
└── runs/<run-id>/                   # run-id = <variant>-<utc timestamp>
    ├── meta.json                    # scenario, variant, image tag, timings
    ├── events.txt
    ├── logs/<pod>.<container>.log
    ├── rollout.json
    ├── readiness.json
    ├── services.json
    ├── endpoints.json
    ├── checks.json                  # per-check PASS/FAIL/NA + reason + weight
    └── report.txt                   # human-readable summary + score
```

---

## 5. Milestone 1 — healthy base deployment end to end

### M1 task list

1. **M0 bootstrap**
   - `git init`; add `.gitignore` (`.state/`, `__pycache__/`, `*.pyc`, `.venv/`,
     `.pytest_cache/`, `*.egg-info/`).
   - `pyproject.toml`: deps `fastapi`, `uvicorn[standard]`; dev deps `pytest`,
     `httpx` (for `TestClient`), `ruff` (optional). pytest config.
   - `config/versions.env` with finalized pins (see §2).
   - `README.md` with quickstart.

2. **App** (`app/main.py`)
   - FastAPI app; `GET /` and `GET /health` per SPEC §8.1.
   - Version read from `APP_VERSION` env, default `"0.1.0"`.
   - `app/__init__.py` exports `__version__`.

3. **Unit tests** (`tests/test_health.py`)
   - `GET /health` → 200, body exactly `{"status": "ok"}`.
   - `GET /` → 200, has `name` == `"pipelinefixrl-app"` and a `version` string.
   - Unknown route → 404.
   - Run with `pytest -q`.

4. **Dockerfile** (`docker/Dockerfile`)
   - Stage 1: `python:3.12-slim`, install deps into a venv / `--prefix`.
   - Stage 2: `python:3.12-slim`, copy deps + `app/`, create non-root user
     `app` (UID 1000), `USER 1000`, `EXPOSE 8000`,
     `CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]`.
   - `.dockerignore` (`.state/`, `.git/`, tests, docs, charts).
   - Compatible with `readOnlyRootFilesystem: true` (no writes outside `/tmp`).

5. **Helm chart** (`charts/app/`)
   - `Chart.yaml` (apiVersion v2, appVersion `0.1.0`).
   - `values.yaml` per SPEC §8.3.
   - `templates/_helpers.tpl` (name/labels helpers).
   - `templates/deployment.yaml`: 1 replica, probes from values, security
     context from values, resources from values, `emptyDir` for `/tmp` since
     rootfs is read-only.
   - `templates/service.yaml`: ClusterIP, port 80 → targetPort 8000.
   - `helm lint charts/app` clean.

6. **kind lifecycle scripts**
   - `scripts/lib.sh`: resolve `REPO_ROOT`, export
     `KUBECONFIG="$REPO_ROOT/.state/kubeconfig"`, source `versions.env`, logging
     helpers, `require_cmd` guard.
   - `scripts/kind-cluster.yaml`: `kind` `Cluster` config, one
     `control-plane` node, `image: <KIND_NODE_IMAGE with @sha256>`.
   - `scripts/kind-up.sh`: create `.state/`; if cluster absent,
     `kind create cluster --name $KIND_CLUSTER_NAME --config
     scripts/kind-cluster.yaml --kubeconfig "$KUBECONFIG"`; wait for node Ready;
     snapshot metadata to `.state/clusters/`.
   - `scripts/kind-down.sh`: `kind delete cluster --name $KIND_CLUSTER_NAME
     --kubeconfig "$KUBECONFIG"`; remove `.state/clusters/*`, `.state/kubeconfig`.

7. **Harness — deploy + verify path** (subset of full harness)
   - `harness/cli.py` with subcommands: `deploy-base`, `collect`, `evaluate`,
     `run` (chains them), `cleanup-ns`.
   - `harness/run.py` `deploy_base()`:
     namespace create → `docker build` → `kind load docker-image` →
     `helm upgrade --install` (into ns, unique tag) → `kubectl rollout status`.
   - `harness/collect.py`: writes the 6 artifact groups (SPEC §5 step 6).
   - `harness/evaluate.py`: the 7 checks (SPEC §7.1); writes `checks.json` +
     `report.txt`; exit code 0 iff score == 100 and no FAIL.
   - `http_health_ok` method (fixed): `kubectl run pfrl-probe --rm -i --restart=Never
     --image=<same app image> -- python -c "<urllib GET to service DNS>"`, or a
     `busybox wget`; chosen impl uses the app image itself (already loaded) to
     avoid pulling anything. Falls back to `kubectl port-forward` if `run` fails.
   - `harness/api.py`: FastAPI with `POST /runs` (body: variant) → executes
     `run.py`, returns run-id + score; `GET /runs/{id}` → report JSON. Thin
     wrapper; the Make targets are the primary interface for M1.

8. **Makefile targets**
   - `setup` — create `.venv`, `pip install -e .[dev]`.
   - `doctor` — verify docker daemon, kind/helm/kubectl/make/python versions,
     digest match. Non-zero exit on any problem.
   - `kind-up` / `kind-down` — call the scripts.
   - `test` — `pytest -q`.
   - `lint` — `helm lint`, `ruff` (if present).
   - `build` — docker build with unique tag; print tag.
   - `deploy-base` — `python -m harness run --variant base`.
   - `verify-base` — assert last run's score == 100 (reads `.state/runs`).
   - `clean-ns` — delete the run namespace.
   - `e2e-base` — `doctor` → `kind-up` → `test` → `deploy-base` → `verify-base`
     → `clean-ns`.
   - `eval` — (M2) run scenario suite then `kind-down`.

### M1 acceptance criteria (exact)

All commands run from `C:\micro` in Git Bash, Docker Desktop running.

- **A1 — doctor.** `make doctor` exits `0` and prints: docker server reachable;
  `kind version` == `KIND_VERSION`; `helm version` ≥ `HELM_MIN_VERSION`;
  node-image digest in `kind-cluster.yaml` == `versions.env`.
- **A2 — unit tests.** `make test` → pytest reports all tests passed, `0`
  failures, exit `0`. At least the 4 cases in M1 task 3 exist.
- **A3 — cluster up.** `make kind-up` creates cluster `pipelinefixrl`.
  `kubectl --kubeconfig .state/kubeconfig get nodes` shows exactly `1` node,
  `STATUS=Ready`, and its `.status.nodeInfo` / image matches the pinned version.
  `~/.kube/config` is **unchanged** (verified by mtime + checksum before/after).
- **A4 — isolation.** `.state/kubeconfig` exists and access is restricted to the
  current user. On this Windows/NTFS host POSIX `600` bits are cosmetic, so
  `kind-up.sh` also runs `icacls <kubeconfig> /inheritance:r /grant:r
  "$USERNAME:F"`; the resulting `icacls` listing must show **only**
  `<user>:(F)`. No file outside `.state/` and the repo tree is written by any
  make target (spot check: `~/.kube/`, `~/.aws/` untouched / absent).
- **A5 — build + load.** `make build` produces an image whose tag matches
  `pipelinefixrl/app:base-<sha12>-<timestamp>` and is **not** `latest`.
  `docker exec <kind-node> crictl images` (or `kind load` success output) shows
  the image present in the node after `deploy-base`.
- **A6 — deploy + rollout.** `make deploy-base` completes with
  `kubectl rollout status deploy/app -n pfrl-base-<run-id>` = success within
  `DEPLOY_TIMEOUT_SECONDS`. `helm status` in that namespace = `deployed`.
- **A7 — readiness + endpoints.** In the run namespace:
  `kubectl get deploy app -o jsonpath='{.status.readyReplicas}'` == `1`;
  `kubectl get endpoints app -o json` has ≥1 address with the target port.
- **A8 — health end to end.** The `http_health_ok` check performs a real request
  to the Service and receives HTTP `200` with body `{"status":"ok"}`; recorded in
  `.state/runs/<run-id>/checks.json`.
- **A9 — score.** `.state/runs/<run-id>/checks.json` shows every weighted check
  `PASS` and `report.txt` shows `SCORE: 100`. `make verify-base` exits `0`.
- **A10 — artifacts.** `.state/runs/<run-id>/` contains non-empty `events.txt`,
  `logs/`, `rollout.json`, `readiness.json`, `services.json`, `endpoints.json`,
  `checks.json`, `report.txt`.
- **A11 — namespace teardown.** `make clean-ns` deletes the run namespace;
  `kubectl get ns` no longer lists it; no other namespace affected.
- **A12 — cluster teardown.** `make kind-down` deletes the cluster;
  `kind get clusters` (project kubeconfig) does not list `pipelinefixrl`;
  `.state/kubeconfig` and `.state/clusters/*` removed. `~/.kube/config` still
  unchanged.
- **A13 — one-shot.** From a clean tree (no cluster), `make e2e-base` runs
  A1→A9 in sequence and exits `0`.
- **A14 — re-runnable.** Running `make e2e-base` twice in a row (with
  `kind-down` between, or idempotent `kind-up`) yields the same PASS/score with a
  new unique image tag and new run-id.

---

## 6. Milestone 2 — scenario-001 (incorrect readiness probe path)

### M2 task list

1. `harness/scenarios/scenario-001/scenario.yaml` per SPEC §7.3.
2. `harness/scenarios/scenario-001/task.md` — agent-facing: symptom description
   ("pods never become Ready, Service has no endpoints"), the allowed edit
   surface (`charts/app/values.yaml`), and the definition of done (score 100).
   Does **not** reveal the one-line fix.
3. `break.patch` — unified diff on `charts/app/values.yaml`:
   `readinessProbe.httpGet.path: /health` → `/health2`.
4. `golden.patch` — unified diff reverting `/health2` → `/health`, applied on the
   broken tree (`golden` variant = base + break.patch + golden.patch).
5. Harness scenario support (`harness/scenario.py`):
   - `run_scenario(scenario_id, variant)`: copy base tree to
     `.state/runs/<run-id>/tree/` → apply the selected patch(es) with
     `patch -p1` (byte-exact on this host; `git apply` fallback) → build/load/
     deploy from that tree into a per-variant namespace → collect → `evaluate`
     → compare against `scenario.yaml`'s `expect.<variant>` block
     (`must_fail` / `must_pass` / `score_min` / `score_max` / `evidence` /
     `anticheat_clean`); writes `verdict.json`.
   - `_anticheat(tree)` (SPEC §7.2): probes present in values, `securityContext`
     `runAsNonRoot`/`allowPrivilegeEscalation`/`readOnlyRootFilesystem` intact,
     `replicaCount != 0`, no `tests/**` byte change. Golden must not trip it.
   - `compose_check(scenario_id)`: applies break + golden to a fresh base-tree
     copy and asserts every source file is byte-identical to base.
6. Make targets: `scenario-001-broken`, `scenario-001-golden`,
   `scenario-001-compose`, `scenario-001` (all three, each variant's namespace
   deleted after its run), `e2e-scenario-001` (`doctor kind-up scenario-001`),
   and `eval` (`harness eval --id <SID>`: broken + golden + compose).
7. `README.md` updated with the scenario workflow.

### M2 acceptance criteria (exact)

- **B1 — broken deploys but fails readiness.** `make scenario-001-broken`:
  namespace `pfrl-scenario-001-<run-id>` created; `helm_release_ok=PASS`;
  `rollout_complete=FAIL` (times out at `DEPLOY_TIMEOUT_SECONDS`);
  `deployment_ready=FAIL` (`readyReplicas` = `0` or null);
  `endpoints_present=FAIL` (no ready addresses);
  `http_health_ok=FAIL`.
- **B2 — broken score.** `report.txt` `SCORE` ≤ `60` and ≤
  `scenario.yaml.expect.broken.score_max`. Harness exits **non-zero** for the
  broken variant only if run as a "must be healthy" check; when run as
  `scenario-001-broken` it exits `0` because the failure **matches** the
  expectation (`must_fail` all satisfied). This distinction is explicit in the
  target.
- **B3 — broken evidence.** `.state/runs/<run-id>/events.txt` contains at least
  one `Warning` `Unhealthy` event referencing the readiness probe and HTTP
  `404`; pod logs show `GET /health2` 404 lines. Artifacts non-empty as in A10.
- **B4 — golden full score.** `make scenario-001-golden`: all weighted checks
  `PASS`; `report.txt` `SCORE: 100`; matches
  `scenario.yaml.expect.golden.must_pass` and `score_min: 100`; target exits `0`.
- **B5 — golden is a minimal, honest fix.** `golden.patch` touches only
  `charts/app/values.yaml` and only the `readinessProbe.httpGet.path` line
  (verified by `git apply --stat` / diff line count == 1 changed hunk). Anti-cheat
  check reports no violation.
- **B6 — break/golden compose.** Applying `break.patch` then `golden.patch` to
  the base tree yields a tree byte-identical to base (`git diff` empty).
- **B7 — comparison output.** `make scenario-001` runs `scenario-001-broken`,
  `scenario-001-golden`, `scenario-001-compose` in order. Each variant writes
  `.state/runs/<run-id>/verdict.json` (`score`, `expected`, `evidence`,
  `anticheat_violations`, `matches_expectation`) and prints its `SCORE` +
  `expectation : MATCH/MISMATCH` line. The aggregate target exits `0` only if
  **all three** sub-targets exit `0` (i.e. broken matched its expectation,
  golden scored 100, patches composed to base).
- **B8 — isolation & cleanup.** Each variant runs in its own namespace; both
  namespaces are deleted after their runs (`kubectl get ns` clean); `make eval`
  deletes the cluster at the end. `~/.kube/config` unchanged throughout.
- **B9 — no test/probe weakening.** No file under `tests/` changed by either
  patch; both probes still present in the deployed manifest for the golden
  variant (`kubectl get deploy -o json` shows `readinessProbe` and
  `livenessProbe`).
- **B10 — determinism.** Running `make scenario-001` twice yields identical
  PASS/FAIL sets and identical scores (image tags/run-ids differ only).

---

## 7. Sequencing & stop points

1. **M0** bootstrap + finalize version pins. **DONE** (2026-08-28, commit
   `67c767f`; kind `v0.33.0`, node `v1.37.0@sha256:a1ed56cf…`).
2. **M1** app → tests → Docker → chart → kind scripts → harness deploy/verify →
   Makefile. **DONE** — A1–A14 in `docs/M1_EVIDENCE.md` (commit `e112ede`,
   cold-run addendum `60a5272`).
3. **M2** scenario-001 broken + golden. **DONE** — B1–B10 in
   `docs/M2_EVIDENCE.md`. Fault: `readinessProbe.httpGet.path` `/health` →
   `/health2`. Broken SCORE 10 (≤ 60), golden SCORE 100, patches compose to base.
4. Only after M1 **and** M2 pass end to end: design scenarios 002–010 (separate
   plan revision). **Not started.**

Nine remaining scenarios and the baseline/advanced agents are **not** started
before step 4.

## 8. Risks / open items

| Risk                                                              | Mitigation                                                        |
|------------------------------------------------------------------ |----------------------------------------------------------------- -|
| `winget` install of make/kind/helm unreliable in this shell       | Fallback: `go install` for kind; GnuWin32/`choco` for make; document manual steps in README. |
| Docker daemon not running                                         | `make doctor` fails fast with the exact "start Docker Desktop" message. |
| kind node-image digest drift / wrong digest                       | Digest copied from official release notes at M0, checked by `make doctor`, recorded in commit. |
| `readOnlyRootFilesystem` breaks uvicorn/tmp writes                | `emptyDir` volume mounted at `/tmp`; verified in M1. |
| Windows path / line-ending issues in bash scripts + patches       | `.gitattributes` forcing `LF` for `scripts/**`, `*.patch`, `*.sh`. |
| `kubectl run` probe pod can't resolve Service DNS immediately     | Retry with backoff; `port-forward` fallback path in `http_health_ok`. |
| kind's default CNI (`kindnetd`) does **not** enforce `NetworkPolicy` | No planned scenario relies on NetworkPolicy enforcement. Networking faults (scenario-005) use Service wiring, not policy. A future policy scenario would need a CNI swap and is explicitly out of scope. |
| Degraded-but-serving faults (security 006, networking 005, observability 008, CI 009) score higher than a dead pod | Grading principle in §11.1: per-scenario `score_max` is set strictly below the weakest legitimate partial fix; `must_fail` lists pin the diagnostic. |

## 9. Decisions (resolved 2026-08-28)

1. **Missing tools** — I install kind, helm, make via `winget`
   (`Kubernetes.kind`, `Helm.Helm`, `ezwinports.make`); `go install` / GnuWin32
   are documented fallbacks. `make doctor` verifies versions.
2. **kind pin** — pin the **newest stable kind release** available at M0; its
   `kindest/node` `@sha256` digest is copied verbatim from that release's notes
   and recorded in the M0 commit.
3. **Interface** — `Makefile` stays the canonical entrypoint; recipes call
   `bash scripts/*.sh` via Git Bash. No PowerShell wrapper unless `winget` make
   proves unreliable.

## 10. Approval gates (status)

- **M0 / M1** — approved and complete (commits `67c767f`, `e112ede`, `60a5272`;
  evidence `docs/M1_EVIDENCE.md`).
- **M2 / scenario-001** — approved and complete (commit `62b74ad`; evidence
  `docs/M2_EVIDENCE.md`).
- **M-BE (base-evolution)** — planned in §11.7. **Not implemented.** The one
  milestone that changes the deployable base; lands after M2, before M3.
- **scenario-002 … scenario-010** — planned in §11 below (M3…M11). **Not
  implemented.** Awaiting explicit review/approval of the §11 matrix before any
  M-BE change, scenario file, harness check, or Makefile target is created.

---

## 11. Scenario roadmap — scenario-002 … scenario-010 (PLANNED, not implemented)

scenario-001 is frozen. This section is design-only: no code, no patches, no
harness changes are produced until the matrix in §11.2 is approved. Work ships
as one prerequisite milestone **M-BE** (base-evolution, §11.7) followed by
**M3 = scenario-002 … M11 = scenario-010**, built and demonstrated one at a
time, in number order, never in parallel.

### 11.1 Design principles

1. **Deterministic, offline, kind-only.** One control-plane node, one replica,
   base resource budget (`50m`/`64Mi` requests, `250m`/`128Mi` limits) unless a
   scenario is explicitly about resources. No registry pulls, no LoadBalancer,
   no PersistentVolume, no second node, no cloud. Every failure reproduces with
   the host network unplugged.
2. **One fault, one domain.** Each scenario injects exactly one root cause that
   foregrounds a different DevOps discipline. `break.patch` is the smallest diff
   that produces it; `golden.patch` is the smallest diff that removes it, and
   `break.patch` + `golden.patch` applied to the base tree is byte-identical to
   base (same B6 rule as scenario-001).
3. **Grading always sums to 100.** The functional backbone — `helm_release_ok`,
   `rollout_complete`, `deployment_ready`, `pods_ready`, `endpoints_present`,
   `http_health_ok` — is reused. A scenario may reallocate up to ~45 of those
   points to one or more domain-specific checks (§11.3). `no_bad_events` stays
   weight 0 (report-only).
4. **Broken threshold is calibrated, not fixed at 60.** A total outage lands
   `≤ 10`; a degraded-but-serving fault (security posture 006, Service wiring
   005, log-schema 008, CI contract 009) lands higher but strictly below the
   weakest legitimate partial fix, with a `must_fail` list that pins the specific
   diagnostic. Golden is always exactly `100`.
7. **Shared base frozen after M-BE.** All base capabilities scenarios 002–010
   need land in one prerequisite milestone (§11.6–§11.7); from M3 on, a scenario
   adds only its `harness/scenarios/<id>/` files and append-only checks.
5. **Anti-cheat is per scenario and additive to §7.2.** Every scenario keeps the
   base rules (probes present, `securityContext` `runAsNonRoot` /
   `allowPrivilegeEscalation` / `readOnlyRootFilesystem` intact, no `tests/**`
   byte change, `replicaCount ≥ 1`, no `:latest`) and adds rules that block the
   scenario's specific shortcut (e.g. "don't hardcode the image tag", "don't
   delete the resource limits", "don't edit the failing test").
6. **CLI-first diagnosis.** Each scenario names the exact `kubectl` / `helm` /
   `docker` / `git` commands that reveal the root cause, and the evidence
   substrings the harness scans for in `events.*`, `logs/*.log`, `pods.json`,
   and (new) `build.log` / `ci.log`.

### 11.2 Proposed scenario matrix

| # | Title | Primary domain | Secondary | Injected fault | `break.patch` target | App serving while broken? | Broken `score_max` | Golden | New checks | Difficulty |
|---|-------|----------------|-----------|----------------|----------------------|---------------------------|-------------------:|-------:|------------|------------|
| 001 | Incorrect readiness probe path | Kubernetes probes | config, debugging | readiness `httpGet.path` `/health`→`/health2` → 404, never Ready | `charts/app/values.yaml` (1 line) | no | 60 (actual 10) | 100 | — | **intro** |
| 002 | Wrong pinned image tag (`Never` policy) | Docker / image distribution | Helm values | `image.tagOverride` set to a tag that was never `kind load`ed; `pullPolicy: Never` forbids fetching it → `ErrImageNeverPull` | `charts/app/values.yaml` (1 line) | no | 10 | 100 | `image_pull_ok` | easy |
| 003 | OOMKilled crash loop | Runtime resources | Kubernetes, debugging | `resources.requests/limits.memory`→`16Mi` → OOMKill on startup, `CrashLoopBackOff` | `charts/app/values.yaml` (2 lines) | no (flapping) | 10 | 100 | `no_oomkill` | easy–medium |
| 004 | Helm value not wired to template | Helm templating | config | `deployment.yaml` image ref points at `.Values.image.version` (undefined) → empty tag → `InvalidImageName` | `charts/app/templates/deployment.yaml` (1 line) | no | 10 | 100 | — (reuses functional) | medium |
| 005 | Service selects no pods | Networking | Kubernetes Service | `service.yaml` `selector` hardcoded to a non-matching label set → 0 endpoints | `charts/app/templates/service.yaml` (1 hunk) | yes | 55 | 100 | `service_selects_pods` | medium |
| 006 | Container runs as root | Security | Pod Security | `securityContext` weakened: `runAsNonRoot:false`, `runAsUser:0`, `readOnlyRootFilesystem:false`, `allowPrivilegeEscalation:true` | `charts/app/values.yaml` (4–5 lines) | yes | 55 | 100 | `runs_as_nonroot`, `readonly_rootfs`, `no_priv_escalation`, `caps_dropped` | medium |
| 007 | Misconfigured ConfigMap reference | Configuration | Kubernetes ConfigMap | `config.key` `tier`→`teir` → `configMapKeyRef` miss → `CreateContainerConfigError` | `charts/app/values.yaml` (1 line) | no | 10 | 100 | `config_applied` | medium |
| 008 | Structured-log format regression | Observability | debugging | `logFormat` `json`→`plain`; service stays 100% healthy but stdout is no longer machine-parseable → log-schema / access-log checks fail | `charts/app/values.yaml` (1 line) | **yes (fully healthy)** | 65 | 100 | `structured_logs_ok` | medium |
| 009 | CI gate + health contract regression | CI/CD | Git, testing | `app/main.py` `/health` body `{"status":"ok"}`→`{"status":"healthy"}` → `make ci` (pytest) red, `http_health_ok` red, pod still Ready | `app/main.py` (1 line) | yes | 50 | 100 | `ci_gate_pass` | medium–hard |
| 010 | Unresolved merge conflict | Git | Docker / build | conflict markers injected into `requirements.txt` → `pip install` → `docker build` fails, nothing deploys | `requirements.txt` (~5 lines) | no build | 10 | 100 | `image_build_ok`, `git_tree_resolved` | medium |

Domain coverage across 001–010: **Kubernetes** 001/003/005/007/008 · **Helm**
002/004 · **Docker** 002/003/010 · **Git** 009/010 · **CI/CD** 009 ·
**configuration** 001/004/007 · **networking** 005 · **security** 006 ·
**observability** 008 · **debugging** every scenario (esp. 003/008/009/010). No
scenario uses AWS, EKS, ECR, IAM, or any external cloud resource.

### 11.3 Additional deterministic checks introduced (M3+)

All are pure functions of collected artifacts; none use an LLM. Added to
`harness/evaluate.py` when the owning scenario is built; weights are taken out of
the functional backbone so each scenario still totals 100.

| Check | Owning scenario(s) | PASS condition | Source artifact |
|-------|--------------------|----------------|-----------------|
| `image_pull_ok` | 002 | no container `state.waiting.reason` in {`ErrImageNeverPull`,`ImagePullBackOff`,`ErrImagePull`,`InvalidImageName`} (scenario-002 produces `ErrImageNeverPull` specifically) | `pods.json` |
| `no_oomkill` | 003 | no container `lastState.terminated.reason == OOMKilled`; total `restartCount ≤ threshold` | `pods.json` |
| `structured_logs_ok` | 008 | ≥ 95 % of non-blank stdout lines parse as JSON objects; every parsed object has keys `{ts, level, msg}`; ≥ 1 object is an HTTP access record with `{method, path, status}` and `status` 2xx for a synthetic `GET /health` through the Service | `logs/*.log` + live HTTP |
| `service_selects_pods` | 005 | Service `.spec.selector` is non-empty **and** equals the Deployment `.spec.selector.matchLabels`; ≥1 EndpointSlice endpoint `targetRef` resolves to a Ready pod of that Deployment | `services.json`, `rollout.json`, `endpointslices.json`, `pods.json` |
| `runs_as_nonroot` | 006 | applied container `securityContext.runAsNonRoot == true` **and** `runAsUser >= 1000` | `pods.json` |
| `readonly_rootfs` | 006 | applied container `securityContext.readOnlyRootFilesystem == true` | `pods.json` |
| `no_priv_escalation` | 006 | applied container `securityContext.allowPrivilegeEscalation == false` | `pods.json` |
| `caps_dropped` | 006 | applied container `securityContext.capabilities.drop` contains `ALL`; `add` is empty/absent | `pods.json` |
| `config_applied` | 007 | `GET /` through the Service returns 200 with the config-sourced field (`tier`) equal to the ConfigMap's value; ConfigMap volume/`valueFrom` still present in the rendered manifest | live HTTP + `rollout.json` |
| `ci_gate_pass` | 009 | `scripts/ci.sh` on the submitted tree exits 0 — runs `pytest -q`, `helm lint charts/app`, `docker build`, and a `:latest`/pin-policy grep | `ci.log` |
| `image_build_ok` | 010 | `docker build` for the variant tree exits 0 | `build.log` |
| `git_tree_resolved` | 010 | no tracked file contains a conflict marker line (`^<<<<<<< `, `^=======$`, `^>>>>>>> `) | variant `tree/` |

### 11.4 Harness / check additions (by scenario, appended when that scenario is built)

**All shared-base changes are consolidated into milestone M-BE (§11.7) and land
before scenario-002.** After M-BE the deployable base — `app/`, `charts/app/`,
`docker/`, `requirements.txt`, `scripts/`, and every existing `Makefile` recipe —
is **frozen**; no scenario milestone (M3–M11) may modify it. Each scenario
milestone adds only: its four files under `harness/scenarios/<id>/`, one or more
**append-only** pure-function checks in `harness/evaluate.py`, and (already
declared in M-BE) its Make targets.

| Scenario | Scenario-local addition |
|----------|------------------------|
| 002 | `image_pull_ok` check (reads `pods.json`). Relies on M-BE's `image.tagOverride` chart knob. |
| 003 | `no_oomkill` check (reads `pods.json`). |
| 004 | anti-cheat helper to grep chart templates (`.Values.image.tag` referenced; no literal tag). |
| 005 | `service_selects_pods` check (structural selector compare). |
| 006 | `runs_as_nonroot`, `readonly_rootfs`, `no_priv_escalation`, `caps_dropped` checks (read applied `pods.json`). |
| 007 | `config_applied` check (live HTTP + rendered manifest). Relies on M-BE's `app-config` ConfigMap + `configMapKeyRef` + `/`-echoes-`tier`. |
| 008 | `structured_logs_ok` check (parses `logs/*.log` + one synthetic request). Relies on M-BE's `logFormat` config + JSON logging formatter. |
| 009 | `ci_gate_pass` check (runs M-BE's `scripts/ci.sh`). Relies on M-BE's `make ci`. |
| 010 | `image_build_ok`, `git_tree_resolved` checks; the "build failed → score from those two, skip deploy" runner path (added in M-BE as a harness generalisation, exercised first here). |

### 11.5 Per-scenario specifications

Each block gives the twelve required items: failure mode · initial broken state
& user task · CLI investigation workflow · expected symptoms & diagnostic
evidence · `break.patch` scope · minimal `golden.patch` · deterministic weighted
checks · broken threshold & golden 100 · anti-cheat rules · runtime/resource
budget · difficulty & why it differs from scenario-001.

---

#### scenario-002 — Wrong pinned image tag (`Never` policy)

- **No network or public registry anywhere.** The scenario runner builds and
  `kind load docker-image`s the correct uniquely-tagged image
  (`pipelinefixrl/app:scenario-002-broken-<sha>-<ts>`) exactly as for every other
  scenario, and deploys with `--set image.tag=<that>`. The fault is that the
  chart is told to use a *different, un-loaded* tag.
- **Failure mode.** `charts/app/values.yaml` `image.tagOverride` (an M-BE chart
  knob; empty by default; the deployment template renders
  `{{ .Values.image.tagOverride | default .Values.image.tag }}`) is set to a tag
  that was never built or loaded (e.g. `v0.0.0-not-loaded`). With
  `imagePullPolicy: Never`, the kubelet is forbidden from fetching it and the
  image is absent from the node's containerd store → **`ErrImageNeverPull`**,
  pod stuck `Pending`, `0/1`.
- **Initial broken state & task.** Release installs; Deployment created; pod
  `Pending` / `ErrImageNeverPull`. Task: make the deployment use the image the
  pipeline actually built and loaded — no registry, no `pullPolicy` change.
- **CLI workflow.** `kubectl get pods` (`ErrImageNeverPull`) →
  `kubectl describe pod <p> -n <ns>` (`Container image
  "pipelinefixrl/app:v0.0.0-not-loaded" is not present with pull policy of
  Never`) → `helm get manifest app -n <ns> | grep image:` (the rendered tag is
  the override, not the `--set` tag) → `helm get values app -n <ns>` (spot
  `tagOverride: v0.0.0-not-loaded`) →
  `docker exec pipelinefixrl-control-plane crictl images` (only the
  pipeline-built tag is present) → clear the override.
- **Symptoms & evidence.** `pods.json`:
  `containerStatuses[].state.waiting.reason == ErrImageNeverPull`. `events.*`:
  reason `Failed`, message `… is not present with pull policy of Never`.
  Evidence scan: `events_contains: ["ErrImageNeverPull", "pull policy of Never"]`,
  `pods_json_contains: ["ErrImageNeverPull"]`.
- **`break.patch` scope.** One line in `charts/app/values.yaml`:
  `tagOverride: ""` → `tagOverride: "v0.0.0-not-loaded"`.
- **Minimal `golden.patch`.** One line: `tagOverride: "v0.0.0-not-loaded"` →
  `tagOverride: ""`.
- **Weighted checks.** helm 10 / rollout 15 / deployment_ready 15 / pods_ready
  10 / endpoints 15 / http 20 / `image_pull_ok` 15 = 100. Broken: only
  `helm_release_ok` PASS → **score 10**.
- **Thresholds.** Broken `score_max: 10`; golden `score_min: 100`.
  `must_fail: [rollout_complete, deployment_ready, pods_ready, endpoints_present,
  http_health_ok, image_pull_ok]`.
- **Anti-cheat.** `image.tagOverride` is empty **or** equal to a tag the runner
  recorded as loaded into the node (`meta.json`); `image.pullPolicy` ∈
  {`Never`,`IfNotPresent`} — **the fix may not switch to `Always`**;
  `image.repository` unchanged; no `imagePullSecrets` / registry / init-container
  / sidecar; base §7.2 rules.
- **Budget.** `deploy_timeout` 45 s; `ErrImageNeverPull` is immediate (no pull
  backoff); wall time ≤ 3 min; 1 pod, base resources; zero network.
- **Difficulty: easy.** Same "one wrong value in `values.yaml`" shape as
  scenario-001, in the image-distribution domain. Distinct from scenario-004:
  004 renders an *empty* tag from an undefined template value → `InvalidImageName`
  (a Helm templating bug); 002 renders a *non-empty but un-loaded* tag under a
  `Never` policy → `ErrImageNeverPull` (a `kind load` / pinned-tag contract
  break). Different `waiting.reason`, different fix (clear an override vs repair a
  template reference).

---

#### scenario-003 — OOMKilled crash loop

- **Failure mode.** `resources.requests.memory` and `resources.limits.memory` are
  both set to `16Mi`. The Python/uvicorn process exceeds the cgroup limit during
  import and is OOM-killed (exit 137) on every start → `CrashLoopBackOff`,
  `RESTARTS` climbing. (Setting both keeps the manifest valid: `requests ≤
  limits`.)
- **Initial broken state & task.** Deployment created; pod cycles
  `ContainerCreating`→`Running`→`OOMKilled`→`CrashLoopBackOff`; `RESTARTS ≥ 2`;
  `0/1` ready. Task: reach a stable Ready pod with `0` restarts **without
  removing resource governance**.
- **CLI workflow.** `kubectl get pods -w` (RESTARTS climbing) →
  `kubectl describe pod <p> -n <ns>` (`Last State: Terminated`, `Reason:
  OOMKilled`, `Exit Code: 137`) → `kubectl logs <p> -n <ns> --previous`
  (startup truncated) → `helm get values app -n <ns>` (`limits.memory: 16Mi`) →
  compare against a known-good size.
- **Symptoms & evidence.** `pods.json`:
  `containerStatuses[].lastState.terminated.reason == OOMKilled`, `exitCode ==
  137`, `restartCount ≥ 2`. `events.*`: `BackOff` "Back-off restarting failed
  container". Evidence scan: `pods_json_contains: ["OOMKilled","137"]`,
  `events_contains: ["BackOff"]`.
- **`break.patch` scope.** Two lines in `charts/app/values.yaml`:
  `requests.memory` and `limits.memory` → `16Mi`.
- **Minimal `golden.patch`.** Two lines: `requests.memory: 64Mi`,
  `limits.memory: 128Mi` (restores the base values exactly).
- **Weighted checks.** helm 10 / rollout 15 / deployment_ready 15 / pods_ready
  15 / endpoints 15 / http 20 / `no_oomkill` 10 = 100. Broken: only
  `helm_release_ok` PASS → **score 10**.
- **Thresholds.** Broken `score_max: 10`; golden `score_min: 100`
  (`restart_threshold: 0`).
- **Anti-cheat.** `resources.requests` and `resources.limits` blocks present and
  non-empty; `limits.memory ≥ 64Mi`; `requests.memory ≥ 32Mi`; neither block
  deleted; base §7.2 rules. Blocks the "delete the limits" shortcut.
- **Budget.** `deploy_timeout` 60 s; ~3 restart cycles in 60–90 s; wall time
  ≤ 3 min; transient memory footprint tiny.
- **Difficulty: easy–medium.** Removes scenario-001's "the current logs show it"
  crutch: the signal lives in `--previous` logs and pod `lastState`. The fix is
  a sizing judgement bounded by anti-cheat (cannot just remove the limit), so the
  learner has to choose a value that actually works.

---

#### scenario-004 — Helm value not wired to the template

- **Failure mode.** `charts/app/templates/deployment.yaml` is edited so the
  container image reference reads `{{ .Values.image.version }}` (a key that does
  not exist) instead of `{{ required "…" .Values.image.tag }}`. Helm renders the
  missing value as empty, so the manifest carries `image:
  "pipelinefixrl/app:"` → the kubelet rejects it with `InvalidImageName`.
- **Initial broken state & task.** `helm upgrade --install` **succeeds** (Helm
  does not validate image refs); Deployment created; pod `Pending` with
  `InvalidImageName`, `0/1`. Task: make the deploy use the tag supplied via
  `--set image.tag=<unique>` — fix the chart, do not hardcode a tag.
- **CLI workflow.** `helm get manifest app -n <ns> | grep image:` (see the empty
  tag) → `helm get values app -n <ns>` (`image.tag` **is** set) →
  `kubectl describe pod` (`InvalidImageName` / `couldn't parse image
  reference`) → inspect `charts/app/templates/deployment.yaml` → spot the wrong
  value path.
- **Symptoms & evidence.** Rendered manifest image ends with `:` (empty tag);
  `pods.json` `state.waiting.reason == InvalidImageName`; `events.*` reason
  `Failed` / `InvalidImageName`. Evidence scan: `events_contains:
  ["InvalidImageName"]`, `manifest_contains: ["pipelinefixrl/app:\""]`.
- **`break.patch` scope.** One line in `charts/app/templates/deployment.yaml`:
  the `image:` expression.
- **Minimal `golden.patch`.** One line: restore `{{ required "image.tag is
  required and must not be 'latest'" .Values.image.tag }}`.
- **Weighted checks.** Functional backbone reused as-is (helm 10 / rollout 20 /
  deployment_ready 20 / pods_ready 15 / endpoints 15 / http 20). Broken: only
  `helm_release_ok` PASS → **score 10**.
- **Thresholds.** Broken `score_max: 10`; golden `score_min: 100`.
- **Anti-cheat.** `templates/deployment.yaml` still references
  `.Values.image.tag`; no literal tag hardcoded (`grep -E ':(latest|v?[0-9])'`
  fails); the `required`/non-empty guard on the tag is present; `image.repository`
  from values unchanged; probe / `securityContext` blocks in the template
  unchanged; base §7.2 rules.
- **Budget.** `deploy_timeout` 45 s; `InvalidImageName` is immediate; wall time
  ≤ 2 min.
- **Difficulty: medium.** First scenario whose bug is in a **template**, not
  `values.yaml`. Requires `helm get manifest` to see rendered output and the
  discipline to distinguish "value supplied" from "value referenced". Anti-cheat
  forbids the tempting "just hardcode the tag" fix.

---

#### scenario-005 — Service selects no pods

- **Failure mode.** `charts/app/templates/service.yaml` `selector:` is replaced
  with a hardcoded label set (`app.kubernetes.io/name: web`) that does not match
  the Deployment's pod labels. The pod is healthy and Ready, but the Service has
  **no endpoints**, so nothing routes to it.
- **Initial broken state & task.** Rollout completes; `deployment_ready` and
  `pods_ready` PASS; `kubectl get endpoints app` is empty; `GET /health` through
  the Service hangs / refuses. Task: restore Service→pod routing without
  touching the Deployment's labels.
- **CLI workflow.** `kubectl get endpoints app -n <ns>` (none) →
  `kubectl get endpointslices -n <ns>` (none) →
  `kubectl get svc app -n <ns> -o jsonpath='{.spec.selector}'` vs
  `kubectl get deploy app -n <ns> -o jsonpath='{.spec.selector.matchLabels}'`
  (mismatch) → `kubectl get pods --show-labels -n <ns>` → inspect
  `templates/service.yaml`.
- **Symptoms & evidence.** `endpoints.json` `subsets` empty;
  `endpointslices.json` `items` empty; Service selector ≠ Deployment selector.
  `http_health_ok` FAIL "service has no ready endpoints". Evidence scan:
  `selector_mismatch: true` (structural, in `verdict.json`).
- **`break.patch` scope.** One hunk in `charts/app/templates/service.yaml`
  replacing the `{{- include "app.selectorLabels" . | nindent 4 }}` line with a
  literal non-matching block.
- **Minimal `golden.patch`.** Restore the `include "app.selectorLabels"` line.
- **Weighted checks.** helm 10 / rollout 15 / deployment_ready 15 / pods_ready
  10 / endpoints 15 / http 20 / `service_selects_pods` 15 = 100. Broken:
  `helm_release_ok`, `rollout_complete`, `deployment_ready`, `pods_ready` PASS;
  `endpoints_present`, `http_health_ok`, `service_selects_pods` FAIL →
  **score 50**.
- **Thresholds.** Broken `score_max: 55`; golden `score_min: 100`.
  `must_fail: [endpoints_present, http_health_ok, service_selects_pods]`.
- **Anti-cheat.** Service `.spec.selector` non-empty and structurally equal to
  the Deployment `.spec.selector.matchLabels`; Deployment selector / pod labels
  unchanged; no second Service or `ExternalName` shim; base §7.2 rules.
- **Budget.** `deploy_timeout` 45 s (rollout succeeds fast); `http_health_ok`
  fails fast on "no endpoints"; wall time ≤ 2 min.
- **Difficulty: medium.** Removes scenario-001's "the pod isn't Ready" crutch —
  here everything workload-side is green and the learner must reason about the
  Service→selector→EndpointSlice chain. Introduces a structural (non-HTTP)
  grading check.

---

#### scenario-006 — Container runs as root

- **Failure mode.** `charts/app/values.yaml` `securityContext` is weakened:
  `runAsNonRoot: false`, `runAsUser: 0`, `readOnlyRootFilesystem: false`,
  `allowPrivilegeEscalation: true` (and `capabilities.drop: [ALL]` removed). The
  app still serves — root can bind `:8000`, the rootfs is writable — so every
  functional check passes, but the runtime posture is non-compliant.
- **Initial broken state & task.** Deployment healthy, pod Ready, `GET /health`
  → 200. The pod runs as UID 0 with a writable rootfs and privilege escalation
  allowed. Task: restore a hardened posture (non-root, read-only rootfs, no
  privilege escalation, all capabilities dropped) **without breaking the app**.
- **CLI workflow.** `kubectl get pod <p> -n <ns> -o
  jsonpath='{.spec.containers[0].securityContext}'` →
  `kubectl exec <p> -n <ns> -- id` (`uid=0(root)`) →
  `kubectl exec <p> -n <ns> -- sh -c 'touch /x && echo writable'` →
  `helm get values app -n <ns>` (weakened block) →
  `kubectl get pod -o yaml | sed -n '/securityContext/,+8p'`.
- **Symptoms & evidence.** `pods.json` container `securityContext`:
  `runAsNonRoot != true` / `runAsUser == 0` / `readOnlyRootFilesystem != true` /
  `allowPrivilegeEscalation == true`. Evidence scan is structural (`pods.json`),
  not string-based.
- **`break.patch` scope.** 4–5 lines in the `securityContext` block of
  `charts/app/values.yaml`.
- **Minimal `golden.patch`.** Restore exactly those 4–5 fields
  (`runAsNonRoot: true`, `runAsUser: 1000`, `allowPrivilegeEscalation: false`,
  `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]`).
- **Weighted checks.** Functional backbone reduced to 55 (helm 10 / rollout 10 /
  deployment_ready 10 / pods_ready 5 / endpoints 10 / http 10) + posture checks
  45 (`runs_as_nonroot` 15 / `readonly_rootfs` 10 / `no_priv_escalation` 10 /
  `caps_dropped` 10). Broken: functional all PASS, four posture checks FAIL →
  **score 55**.
- **Thresholds.** Broken `score_max: 55`; golden `score_min: 100`.
  `must_fail: [runs_as_nonroot, readonly_rootfs, no_priv_escalation,
  caps_dropped]`; `must_pass: [http_health_ok]` (the fix may not disable the
  app).
- **Anti-cheat.** Functional checks must also pass (no "secure by breaking it");
  `podSecurityContext.seccompProfile: RuntimeDefault` retained; `runAsUser`
  numeric and `≥ 1000`; no privileged / `SYS_ADMIN` sidecar; no `emptyDir`
  mounted over `/` to fake read-only; base §7.2 rules.
- **Budget.** `deploy_timeout` 60 s; app healthy so fast; two `kubectl exec`
  calls add a few seconds; wall time ≤ 2 min.
- **Difficulty: medium.** New failure *class*: the deployment is green but
  non-compliant, so "is it Ready?" is not enough — the learner must inspect the
  effective runtime security context and prove non-root. Grading introduces
  posture checks with real weight.

---

#### scenario-007 — Misconfigured ConfigMap reference

- **Base capability (from M-BE).** The chart already ships an `app-config`
  ConfigMap (`tier: standard`) and a container `env` entry via
  `valueFrom.configMapKeyRef: {name: <fullname>-config, key: {{ .Values.config.key }}}`;
  `app/main.py` `/` echoes `tier`. No base change happens in this milestone.
- **Failure mode.** `charts/app/values.yaml` `config.key` is changed from `tier`
  to `teir`. The container's `configMapKeyRef` now points at a missing key →
  kubelet cannot create the container → `CreateContainerConfigError`, pod never
  starts.
- **Initial broken state & task.** Release installs; Deployment created; pod
  `CreateContainerConfigError`, `0/1`. Task: make the app start and serve its
  configured `tier` at `/`, by correcting the reference — not by removing the
  config dependency.
- **CLI workflow.** `kubectl get pods` (`CreateContainerConfigError`) →
  `kubectl describe pod <p> -n <ns>` (`couldn't find key teir in ConfigMap
  <ns>/app-config`) → `kubectl get configmap app-config -n <ns> -o yaml`
  (only `tier` exists) → `helm get values app -n <ns>` / inspect the chart →
  fix the key name.
- **Symptoms & evidence.** `pods.json` `state.waiting.reason ==
  CreateContainerConfigError`; `events.*` message contains `couldn't find key`
  and `app-config`. Evidence scan: `events_contains: ["CreateContainerConfigError",
  "app-config"]`.
- **`break.patch` scope.** One line in `charts/app/values.yaml` (`config.key`).
- **Minimal `golden.patch`.** One line: `teir` → `tier`.
- **Weighted checks.** helm 10 / rollout 15 / deployment_ready 15 / pods_ready
  10 / endpoints 15 / http 20 / `config_applied` 15 = 100. Broken: only
  `helm_release_ok` PASS → **score 10**.
- **Thresholds.** Broken `score_max: 10`; golden `score_min: 100`.
- **Anti-cheat.** Rendered manifest still has the `configMapKeyRef` env (config
  not inlined / hardcoded); the `app-config` ConfigMap still templated and
  non-empty; `app/main.py` still reads `tier` from env (grep); base §7.2 rules.
- **Budget.** `deploy_timeout` 45 s; config error is immediate; wall time
  ≤ 2 min.
- **Difficulty: medium.** Requires tracing a multi-object wiring chain
  (`values` → ConfigMap → `configMapKeyRef` → container env → app), reading a
  `CreateContainerConfigError` that names the missing key, and resolving a
  cross-file name correlation.

---

#### scenario-008 — Structured-log format regression

- **Base capability (from M-BE).** The app emits structured logs controlled by
  `LOG_FORMAT` (`json` default | `plain`): in `json` mode every stdout line is a
  single JSON object with fixed keys `ts` (ISO-8601 UTC), `level`, `logger`,
  `msg`, and — for HTTP access lines — `method`, `path`, `status`. Uvicorn's
  access and error loggers are routed through the same formatter. The chart wires
  `LOG_FORMAT` from `.Values.logFormat`.
- **Failure mode.** `charts/app/values.yaml` `logFormat` is changed from `json`
  to `plain`. The service is **completely healthy** — rollout completes, pod is
  Ready with `0` restarts, endpoints present, `GET /health` → 200 — but stdout is
  now free-text (`INFO: 10.244.0.1:… - "GET /health HTTP/1.1" 200 OK`), so
  nothing downstream can parse it and the observability checks fail.
- **Initial broken state & task.** Deployment green on every Kubernetes signal;
  `kubectl logs deploy/app | jq .` errors on every line. Task: restore
  machine-parseable structured logging **without touching the app or the logging
  formatter** — only the configuration is wrong.
- **CLI workflow.** `kubectl logs deploy/app -n <ns> --tail=20` (lines are
  plain text, not JSON) → `kubectl logs deploy/app -n <ns> | jq .`
  (`parse error: Invalid literal`) →
  `kubectl exec deploy/app -n <ns> -- printenv LOG_FORMAT` (`plain`) →
  `helm get values app -n <ns>` (`logFormat: plain`) → contrast with the base
  default (`json`) → set it back.
- **Symptoms & evidence.** `logs/*.log`: 0 lines parse as JSON;
  `structured_logs_ok` FAIL with reason `"0/NN stdout lines are valid JSON"`.
  All functional checks PASS (`http_health_ok`, `deployment_ready`, … green).
  Evidence scan: `logs_contains: ['"GET /health']` (service *is* serving) plus
  structural `logs_are_json: false` recorded in `verdict.json`.
- **`break.patch` scope.** One line in `charts/app/values.yaml`:
  `logFormat: json` → `logFormat: plain`.
- **Minimal `golden.patch`.** One line: `logFormat: plain` → `logFormat: json`.
- **Weighted checks.** Functional backbone reduced to 65 (helm 10 / rollout 10 /
  deployment_ready 10 / pods_ready 10 / endpoints 10 / http 15) +
  `structured_logs_ok` 35. Broken: functional all PASS, `structured_logs_ok`
  FAIL → **score 65**.
- **Thresholds.** Broken `score_max: 65`; golden `score_min: 100`.
  `must_fail: [structured_logs_ok]`; `must_pass: [http_health_ok,
  deployment_ready, pods_ready]`.
- **Anti-cheat (scenario-specific).** `logFormat` must be exactly `json` (no
  unrecognised value that would trigger a fallback); `app/` and the chart's
  logging wiring are byte-identical to base-v2 (**the fix is the config value,
  not the code**); the healthy-run stdout line count must be ≥ the base healthy
  run's for the same request volume — i.e. `logLevel` may not be raised to
  suppress access logs to "pass" the check; base §7.2 rules.
- **Budget.** `deploy_timeout` 45 s (rollout succeeds fast); one synthetic
  `GET /health` for the access-line assertion; wall time ≤ 3 min; 1 pod, base
  resources.
- **Difficulty: medium.** Distinct from scenario-001 in the opposite direction
  from 006: the deployment is **100 % functionally healthy** — every Kubernetes
  status says "fine" — so the learner cannot lean on readiness/rollout/endpoint
  state at all. They must compare *log output against an expected schema* and
  trace it to a single config value. Introduces log-schema validation as a
  grading check.

---

#### scenario-009 — CI gate + health-contract regression

- **Base capability (from M-BE).** `scripts/ci.sh` (runs `pytest -q`,
  `helm lint charts/app`, `helm template` smoke, `docker build`, and a `:latest`
  / unpinned-base-image grep) and a `make ci` target already exist. No base
  change happens in this milestone; the `break.patch` mutates only the ephemeral
  tree copy.
- **Failure mode.** `app/main.py` `/health` is changed to return `{"status":
  "healthy"}` instead of `{"status": "ok"}`. The readiness probe only checks the
  status code, so the pod still goes Ready — but `pytest`
  (`test_health_returns_ok`) fails, `make ci` goes red, and the harness's
  `http_health_ok` (which asserts the exact body) fails.
- **Initial broken state & task.** `make ci` fails at `pytest` (`1 failed`); if
  deployed anyway, `http_health_ok` FAIL and everything else green. Task: make
  the CI gate green **without weakening the tests**, then deploy to score 100.
- **CLI workflow.** `make ci` (read the pytest assertion diff
  `{'status': 'healthy'} == {'status': 'ok'}`) →
  `git diff <base>..HEAD -- app/main.py` / `git show` (locate the change) →
  fix `app/main.py` → `make ci` again → deploy.
- **Symptoms & evidence.** `ci.log` contains `FAILED
  tests/test_health.py::test_health_returns_ok`; `checks.json` `http_health_ok
  FAIL` with body `{"status": "healthy"}`. Evidence scan: `ci_log_contains:
  ["FAILED","test_health_returns_ok"]`.
- **`break.patch` scope.** One line in `app/main.py` (the `/health` return
  body).
- **Minimal `golden.patch`.** One line: `{"status": "healthy"}` → `{"status":
  "ok"}`.
- **Weighted checks.** Functional 70 (helm 10 / rollout 10 / deployment_ready 10
  / pods_ready 10 / endpoints 10 / http 20) + `ci_gate_pass` 30. Broken:
  `ci_gate_pass` FAIL (30) + `http_health_ok` FAIL (20); rest PASS →
  **score 50**.
- **Thresholds.** Broken `score_max: 50`; golden `score_min: 100`.
  `must_fail: [ci_gate_pass, http_health_ok]`.
- **Anti-cheat.** **No file under `tests/` modified** (cannot fix CI by editing
  the test); `pytest` still collects **≥ 6** tests — a deliberate *lower bound*,
  not the exact base-v2 count of 7 (see §11.7 item 6: the gate must catch a
  deleted test without coupling to future base-vN test additions); `helm lint`
  clean; no `:latest` anywhere; `Chart.yaml` `version` valid semver; `/health`
  handler returns JSON `{"status":"ok"}` (grep) and the app imports; base §7.2
  rules.
- **Budget.** `make ci` ~30–60 s (pytest + lint + cache-warm docker build);
  `deploy_timeout` 60 s; wall time ≤ 4 min; one build, one pod.
- **Difficulty: medium–hard.** First scenario spanning **source + tests +
  pipeline**. "The pod is Ready" is explicitly insufficient — the `/health`
  contract and the CI gate are part of done. The learner runs the gate, reads a
  pytest assertion, uses Git to locate the regression, and anti-cheat blocks the
  "edit the test" shortcut.

---

#### scenario-010 — Unresolved merge conflict

- **Failure mode.** `break.patch` injects Git conflict markers around the
  `fastapi` pin in `requirements.txt`:
  `<<<<<<< HEAD` / `=======` / `>>>>>>> feature/bump-fastapi`. `pip install -r
  requirements.txt` fails (`Invalid requirement: '<<<<<<< HEAD'`) → `docker
  build` fails → no image → nothing deploys.
- **Initial broken state & task.** `docker build` errors during `pip install`;
  the scenario runner records a build failure and does **not** deploy. Task:
  resolve the conflict to a single coherent pinned dependency set, get `docker
  build` green, and deploy to score 100.
- **CLI workflow.** run `docker build` (read the pip error and the offending
  line number) → `git grep -nE '^(<<<<<<< |=======$|>>>>>>> )'` (locate every
  marker) → `git log --oneline --merges` / `git show <merge>` (which side to
  keep) → edit `requirements.txt` to one resolved line → `docker build` again.
- **Symptoms & evidence.** `build.log` contains `<<<<<<<` and `Invalid
  requirement` / `ERROR: … requirements.txt`; `git grep` for markers on the
  broken `tree/` is non-empty. Evidence scan: `build_log_contains:
  ["<<<<<<<","Invalid requirement"]`, `tree_has_conflict_markers: true`.
- **`break.patch` scope.** `requirements.txt` — insert ~5 marker/duplicate
  lines around the `fastapi` pin.
- **Minimal `golden.patch`.** Replace the conflicted block with the single
  resolved line `fastapi>=0.110,<1.0` (net: remove the 4 marker/dup lines).
- **Weighted checks.** `image_build_ok` 20 / `git_tree_resolved` 20 +
  functional 60 (helm 10 / rollout 10 / deployment_ready 10 / pods_ready 10 /
  endpoints 5 / http 15). Broken: build fails → `image_build_ok` FAIL,
  `git_tree_resolved` FAIL, all functional `NA` → **score 0**.
- **Thresholds.** Broken `score_max: 10`; golden `score_min: 100`.
  `must_fail: [image_build_ok, git_tree_resolved]`.
- **Anti-cheat.** Resolved `requirements.txt` still pins `fastapi` and
  `uvicorn[standard]` with bounded specifiers (regex); zero conflict markers in
  any tracked file; `requirements.txt` not deleted / not emptied; `docker build`
  reproducible; base §7.2 rules.
- **Budget.** Failing `docker build` aborts fast at `pip install` (~30–60 s);
  successful build ~60 s; `deploy_timeout` 45 s; wall time ≤ 4 min; one build,
  one pod.
- **Difficulty: medium.** Pure SCM-hygiene failure surfacing as a build error.
  Unlike every other scenario there is **nothing running to inspect** — the
  artifact never builds — so diagnosis is entirely `docker build` output +
  `git grep` + `git log`. Exercises the "read the build log, use Git to
  understand the conflict, resolve by hand" loop and a build-failure grading
  path in the runner.

### 11.6 Base-evolution rule (scenarios 002–010)

1. **All shared-base capabilities land once, in milestone M-BE (§11.7), before
   scenario-002.** Every change any of scenarios 002–010 needs in the deployable
   base — `app/`, `charts/app/`, `docker/`, `requirements.txt`, `scripts/`, the
   `Makefile` — is designed and merged in that single prerequisite milestone.
2. **After M-BE the deployable base is frozen.** No scenario milestone (M3–M11)
   may modify `app/`, `charts/app/`, `docker/`, `requirements.txt`, `scripts/`,
   or any existing `Makefile` recipe. A scenario milestone adds only: its four
   files under `harness/scenarios/<id>/`; one or more **append-only**
   pure-function checks in `harness/evaluate.py` (never edits an existing check
   or shared helper); and — already declared in M-BE — its Make targets.
3. **`break.patch` never mutates the committed base.** As in M2, the runner
   copies the base tree and applies patches to the *copy*; the patch files
   themselves live under `harness/scenarios/<id>/`.
4. **Full regression gate before every scenario milestone.** Re-run and pass:
   `A1–A14`; and for **every previously completed scenario** (001 … N−1) its
   `broken`, `golden`, `compose`, `anti-cheat`, and `namespace + cluster
   teardown` checks. One failure blocks the new scenario until fixed.
5. **All existing patches stay applicable and deterministic.** Every prior
   `break.patch` / `golden.patch` must still apply cleanly (`patch -p1`), produce
   the same PASS/FAIL set and score, and `break + golden` must still compose
   byte-identical to the (now frozen) base. Because the base is frozen after
   M-BE, from M3 onward this is a check, not a risk.

### 11.7 Base-evolution milestone (M-BE) — contents

One commit, landed after M2 and before M3. It is the **only** milestone that
touches the deployable base.

**1 · App (`app/`).** Config-driven structured logging. `app/main.py` (helper in
a new `app/obs.py`) reads `LOG_FORMAT` (`json` default | `plain`) and `LOG_LEVEL`
(`info` default). In `json` mode every stdout line is one JSON object with keys
`ts` (ISO-8601 UTC), `level`, `logger`, `msg`, plus `method` / `path` / `status`
for HTTP access lines; uvicorn's `access` and `error` loggers are attached to the
same handler. `GET /` gains a `tier` field read from env `APP_TIER` (default
`standard`). `GET /health` is unchanged (`{"status":"ok"}`).

**2 · Chart (`charts/app/`).**
- `values.yaml` gains, appended after the existing `env:` block:
  `logFormat: json`, `logLevel: info`, `config: {key: tier, tier: standard}`,
  and in the `image:` block `tagOverride: ""`.
- new `templates/configmap.yaml` → `{{ include "app.fullname" . }}-config` with
  data key `tier: {{ .Values.config.tier | quote }}`.
- `templates/deployment.yaml`: image line becomes
  `{{ .Values.image.tagOverride | default .Values.image.tag }}`; container `env`
  gains `LOG_FORMAT`, `LOG_LEVEL` (from values) and `APP_TIER` via
  `valueFrom.configMapKeyRef: {name: <fullname>-config, key: {{ .Values.config.key }}}`.
- Additions are placed to **minimise patch-context drift**: new template file,
  appended `values.yaml` keys, and one changed line in `deployment.yaml` far from
  the probe / `securityContext` blocks that scenario-001's patch targets.

**3 · CI gate (`scripts/ci.sh` + `make ci`).** `ci.sh` runs `pytest -q`,
`helm lint charts/app`, `helm template` smoke, `docker build`, and a
`:latest` / unpinned-base-image grep; exits non-zero on any failure. New file +
new Makefile recipe.

**4 · Makefile.** Add `ci`; add boilerplate targets `scenario-002 …
scenario-010` (+ `-broken` / `-golden` / `-compose`) and their `.PHONY` entries,
so **no scenario milestone edits the Makefile**.

**5 · Harness generalisations** (not "base", but landed here for one review):
`collect.py` also captures `ci.log`; `scenario.py` `_evidence_scan` reads
`events.*`, `logs/*.log`, `pods.json`, `build.log`, `ci.log`; the runner gains
the "build failed → record `image_build_ok=FAIL`, skip deploy, score from
build/git checks" path (first exercised by scenario-010); `evaluate.py` gains an
append-only check registry (existing 6 checks unchanged).

**6 · Tests (`tests/`).** Add `test_logging.py` (2 tests: JSON formatter key
shape; access-line `method`/`path`/`status` extraction) and `test_root_tier.py`
(1 test: `/` returns `tier`). Existing four tests unchanged → **base-v2 collects
7 tests** (verified: `pytest` → `7 passed`, exit 0). scenario-009's anti-cheat
keeps a **`≥ 6` lower bound** (not `== 7`) *by design*: it must reject a
submission that deletes a test without pinning the count to whatever the base
happens to carry, so future base-vN test additions don't silently tighten an
unrelated scenario's gate. `≥ 6` sits one below the current count and one above
the M1 count (4) plus the two probe/health tests a fix must never remove.

**7 · Regression before M3 is unblocked.** Re-run and pass **`A1–A14`** and
scenario-001 **`broken` / `golden` / `compose` / `anti-cheat` / `teardown`**.
scenario-001's `break.patch` / `golden.patch` are **regenerated in the M-BE
commit only if** the gate reports context drift; the fault (`/health` →
`/health2`) and grading are unchanged and `break + golden` still composes
byte-identical to base-v2. Append an addendum to `M1_EVIDENCE.md` /
`M2_EVIDENCE.md` recording base-v2.

**M-BE runtime:** base edits + `A1–A14` e2e (~5 min) + scenario-001 suite
(~3 min) + new tests (~1 min) ≈ **~15–20 min automated** (plus authoring).

### 11.8 Sequencing & runtime

Order: **M-BE → M3 (002) → M4 (003) → M5 (004) → M6 (005) → M7 (006) →
M8 (007) → M9 (008) → M10 (009) → M11 (010)**. One milestone at a time, in this
order; none started before this matrix is approved.

**Progress (updated 2026-08-29):**
- **M-BE ✅ complete** — commits `7f8a081` (base capabilities) + `e45b97a`
  (`fix: enforce universal image hygiene anti-cheat`). Gate passed: `A1–A14`
  (cold `make e2e-base`, SCORE 100) + scenario-001 broken/golden/compose.
- **M3 / scenario-002 ✅ complete** — commit `1e58242`
  *feat: add deterministic local image pull scenario*. Broken run twice →
  deterministic **SCORE 10** (`ErrImageNeverPull`, `imagePullPolicy: Never`,
  no registry/network pull); golden → **SCORE 100** (only the pinned tag
  repaired to the loaded image); compose-check byte-identical; anti-cheat clean.
  Full regression gate re-run 2026-08-29 against the committed harness:
  scenario-001 (broken 10 / golden 100 / compose) **and** scenario-002
  (broken ×2 = 10 / golden 100 / compose) all PASS; scoped namespace cleanup +
  `scripts/kind-down.sh` clean — no `pfrl-*` namespaces, no kind cluster or
  containers, `.state/kubeconfig` removed, `.state/clusters/` empty, default
  `~/.kube/config` unchanged.
- **M4 / scenario-003 ✅ complete** — commit `bec74c9`
  *feat: add deterministic OOMKilled crash loop scenario*. Broken run twice →
  deterministic **SCORE 10** with `OOMKilled` / exit `137` / `BackOff` evidence
  (`lastState.terminated.reason == OOMKilled`, `exitCode 137`, `CrashLoopBackOff`;
  volatile `restartCount` / event counts differ, all graded outcomes match);
  golden → **SCORE 100** with `no_oomkill` PASS and **0 restarts**
  (`requests/limits.memory` restored to `64Mi`/`128Mi`); compose-check
  byte-identical. Full regression gate re-run 2026-08-29 on the committed
  harness: `A1–A14` (`make e2e-base`, SCORE 100) **and** scenario-001
  (broken 10 / golden 100 / compose) **and** scenario-002
  (broken 10 / golden 100 / compose) all PASS; scoped namespace cleanup +
  `scripts/kind-down.sh` clean — no `pfrl-*` namespaces, no kind cluster or
  containers, `.state/kubeconfig` removed, `.state/clusters/` empty, default
  `~/.kube/config` unchanged.
- **M5 / scenario-004 ✅ complete** — commit `57a853c`
  *feat: add deterministic InvalidImageName scenario*. Broken run twice →
  deterministic **SCORE 10** with `InvalidImageName` and empty-tag evidence
  (`state.waiting.reason == InvalidImageName`, rendered pod image
  `pipelinefixrl/app:`, `events`/`pods.json` contain `InvalidImageName`;
  volatile run-id/namespace/image differ, all graded outcomes match); golden →
  **SCORE 100** with all six backbone checks PASS (template restored to consume
  the `--set image.tag` value); anti-cheat clean (base + golden-only
  `image_ref_wired` rule); compose-check byte-identical. Full regression gate
  re-run 2026-08-29 on the committed harness: `A1–A14` (`make e2e-base`,
  SCORE 100) **and** scenario-001 · scenario-002 · scenario-003 · scenario-004
  (each broken 10 / golden 100 / compose) all PASS; scoped namespace cleanup +
  `scripts/kind-down.sh` clean — no `pfrl-*` namespaces, no kind cluster or
  containers, `.state/kubeconfig` removed, `.state/clusters/` empty, default
  `~/.kube/config` unchanged.
- **M6 / scenario-005 ✅ complete** — commit `ee847c3`
  *feat: add deterministic service selector mismatch scenario*. Mandatory
  pre-implementation gate on `0925933`: `A1–A14` (`make e2e-base`, SCORE 100)
  **and** scenario-001 · scenario-002 · scenario-003 · scenario-004 (each
  broken 10 / golden 100 / compose) all PASS. Broken run twice → deterministic
  **SCORE 50** with identical graded outcomes / evidence — Service selector
  `{app.kubernetes.io/name: web}` ≠ Deployment `matchLabels`
  (`{name: app, instance: app}`), `endpoints_present` FAIL
  (`ready endpoint addresses=0`), `http_health_ok` FAIL
  (`service has no ready endpoints`), `service_selects_pods` FAIL; pod stays
  `Ready` with `0` restarts (`readyReplicas 1`); volatile run-id/namespace/image
  differ only. Golden → **SCORE 100** with all **seven** checks PASS
  (`service_selects_pods`: `selector matches Deployment; 1 ready endpoint(s)`);
  anti-cheat clean (base + golden-only `service_wiring_intact` rule);
  compose-check byte-identical. Post-implementation full regression on the
  committed harness: `A1–A14` (SCORE 100) **and** scenario-001 · scenario-002 ·
  scenario-003 · scenario-004 (broken 10 / golden 100 / compose) · scenario-005
  (broken 50 / golden 100 / compose) all PASS; scoped namespace cleanup +
  `scripts/kind-down.sh` clean — no `pfrl-*` namespaces, no kind cluster or
  containers, `.state/kubeconfig` removed, `.state/clusters/` empty, default
  `~/.kube/config` unchanged.
- **M7 / scenario-006 ✅ complete** — commit `9492bdc`
  *feat: add deterministic runs-as-root pod-security scenario*. Mandatory
  pre-implementation gate on `22eb6c1`: `A1–A14` (`make e2e-base`, SCORE 100)
  **and** scenario-001 · scenario-002 · scenario-003 · scenario-004 ·
  scenario-005 (each broken / golden / compose) all PASS. Broken run twice →
  deterministic **SCORE 55** with identical graded outcomes — container
  `securityContext` weakened in `charts/app/values.yaml` (`runAsNonRoot false`,
  `runAsUser 0`, `allowPrivilegeEscalation true`, `readOnlyRootFilesystem false`,
  `capabilities.drop []`); the pod stays `Ready` with `0` restarts and
  `GET /health -> 200`, so all six functional backbone checks + `no_bad_events`
  PASS, while the four posture checks `runs_as_nonroot` / `readonly_rootfs` /
  `no_priv_escalation` / `caps_dropped` FAIL (15/10/10/10); the universal §7.2
  anti-cheat deterministically lists the three weakened-`securityContext`
  violations (reported, not enforced against the broken reference); volatile
  run-id/namespace/image differ only. Golden → **SCORE 100** with all **eleven**
  checks PASS (`runs_as_nonroot`: `runAsNonRoot=True runAsUser=1000`;
  `caps_dropped`: `drop=['ALL'] add=[]`); anti-cheat clean (base §7.2 +
  golden-only `security_posture_intact` rule — seccomp `RuntimeDefault` retained,
  `runAsUser` ≥ 1000, no privileged / `SYS_ADMIN` container, no volume mounted at
  `/`); compose-check byte-identical. Post-implementation full regression on the
  committed harness: `A1–A14` (SCORE 100) **and** scenario-001 · scenario-002 ·
  scenario-003 · scenario-004 (broken 10 / golden 100 / compose) · scenario-005
  (broken 50 / golden 100 / compose) · scenario-006 (broken 55 / golden 100 /
  compose) all PASS; scoped namespace cleanup + `scripts/kind-down.sh` clean —
  no `pfrl-*` namespaces, no kind cluster or containers, `.state/kubeconfig`
  removed, `.state/clusters/` empty, default `~/.kube/config` unchanged.
- **M8 / scenario-007 ✅ complete** — commit `7393766`
  *feat: add deterministic misconfigured ConfigMap reference scenario*. Mandatory
  pre-implementation gate on `4c7a559`: `A1–A14` (`make e2e-base`, SCORE 100)
  **and** scenario-001 · scenario-002 · scenario-003 · scenario-004 ·
  scenario-005 · scenario-006 (each broken / golden / compose) all PASS. Broken
  run twice → deterministic **SCORE 10** with identical graded outcomes —
  `charts/app/values.yaml` `config.key` `tier`→`teir` breaks the container's
  `APP_TIER` `configMapKeyRef` (key absent from the `app-config` ConfigMap) →
  `CreateContainerConfigError`, pod `Pending` `0/1`; only `helm_release_ok` PASS,
  `rollout_complete` / `deployment_ready` / `pods_ready` / `endpoints_present` /
  `http_health_ok` / `config_applied` all FAIL; evidence scan confirmed
  (`events` contain `couldn't find key` + `app-config`; `pods.json` contains
  `CreateContainerConfigError`; verified against real artifacts:
  `state.waiting == {reason: CreateContainerConfigError, message: "couldn't
  find key teir in ConfigMap <ns>/app-config"}`); universal §7.2 anti-cheat
  clean both runs; volatile run-id/namespace/image and the ungraded weight-0
  `no_bad_events` excluded from the determinism assertion. Golden → **SCORE 100**
  with all eight checks PASS (`config_applied`: `GET / tier='standard'
  want='standard'` — full chain `values → ConfigMap → configMapKeyRef → env →
  app` validated); anti-cheat clean (base §7.2 + golden-only
  `config_wiring_intact` rule — `configMapKeyRef`/`.Values.config.key` retained,
  `app-config` ConfigMap still templated from `.Values.config.tier`,
  `app/main.py` still reads `APP_TIER` from env, `config.tier` non-empty);
  compose-check byte-identical. Post-implementation full regression on the
  committed harness: `A1–A14` (SCORE 100) **and** scenario-001 · scenario-002 ·
  scenario-003 · scenario-004 (broken 10 / golden 100 / compose) · scenario-005
  (broken 50 / golden 100 / compose) · scenario-006 (broken 55 / golden 100 /
  compose) · scenario-007 (broken 10 / golden 100 / compose) all PASS.
- **M9 / scenario-008 ✅ complete** — commit `929ad58` (branch
  `continued-development`) *feat: add deterministic structured-log format
  regression scenario*. Pre-implementation gate on `fc8f7e8`: `A1–A14`
  (`make e2e-base`, SCORE 100) **and** scenario-001…007 (each broken / golden /
  compose) all PASS; scoped teardown + integrity clean. Broken run twice →
  deterministic **SCORE 65** with identical graded outcomes — `values.yaml`
  `logFormat` `json`→`plain` leaves the Deployment 100 % healthy (rollout,
  Ready, 0 restarts, endpoints, `GET /health` 200 → all six functional checks
  PASS) but stdout is free-text, so `structured_logs_ok` FAIL; `verdict.json`
  `logs_are_json: false`; evidence `logs_contains ['"GET /health']` confirms the
  service is serving; universal §7.2 anti-cheat clean both runs. Golden →
  **SCORE 100**, all eight checks PASS; `structured_logs_ok`:
  `5/5 JSON lines; 1 access rec(s); stdout_lines this=20 >= base=16`;
  `verdict.json` `logs_are_json: true`, `stdout_line_count: 20`,
  `base_stdout_line_count: 16` (machine-derived by `make e2e-base` via
  `run.py`'s fixed 10-request synthetic-load `measure_stdout_lines`, not
  hard-coded). Anti-cheat clean (base §7.2 + golden-only
  `structured_logging_intact` — `logFormat` exactly `json`, `logLevel ∈
  {debug,info}` so access lines aren't suppressed, `app/obs.py` byte-identical
  to base-v2, `LOG_FORMAT` still wired from `.Values.logFormat`); compose-check
  byte-identical. Post-implementation full regression on the committed harness:
  `A1–A14` (SCORE 100) **and** scenario-001…007 (broken 10/10/10/10/50/55/10,
  golden 100, compose) **and** scenario-008 (broken 65 / golden 100 / compose)
  all PASS; teardown + integrity clean (no `pfrl-*` ns, no kind cluster /
  containers, `.state/kubeconfig` removed, default `~/.kube/config` unchanged).
  Frozen-base rule respected — `harness/evaluate.py` append-only,
  `harness/run.py` gains one additive `meta["stdout_line_count"]` block,
  `harness/scenario.py` gains the anti-cheat rule + additive verdict keys; no
  `app/` · `charts/` · `docker/` · `requirements.txt` · `scripts/` · `Makefile`
  · `tests/` change.
- **M10 / scenario-009 ✅ complete** — commit `4ebb9c8` (branch
  `continued-development`) *feat: add deterministic CI-gate + health-contract
  regression scenario*. Approach **C** (approved): the check runs the real M-BE
  `scripts/ci.sh` from inside the ephemeral tree — `_TREE_PATHS` gains `scripts/`
  + `config/`, and a new `_assert_frozen_subtrees` guard (run after every patch
  application, both variants) rejects any break/golden patch that touches
  `scripts/` or `config/`. Pre-implementation gate on `98b8440`: `A1–A14`
  (`make e2e-base`, SCORE 100) **and** scenario-001…008 (broken/golden/compose)
  all PASS. Broken run twice → deterministic **SCORE 50** with identical graded
  outcomes — `app/main.py` `/health` `{"status":"ok"}`→`{"status":"healthy"}`
  leaves the pod `Ready` (functional backbone helm/rollout/deployment_ready/
  pods_ready/endpoints PASS) but `http_health_ok` FAIL (body mismatch) and
  `ci_gate_pass` FAIL (`scripts/ci.sh exit 1 (FAILED
  tests/test_health.py::test_health_returns_ok …)`); `ci.log` evidence
  `["FAILED","test_health_returns_ok"]` present; universal §7.2 anti-cheat
  clean. Golden → **SCORE 100**, all eight checks PASS; `ci_gate_pass`:
  `scripts/ci.sh exit 0` (full pytest → helm lint → helm template → docker
  build → pin-policy, `ci: OK`). Anti-cheat clean (base §7.2 + golden-only
  `ci_contract_intact` — tree `scripts/ci.sh` + `lib.sh` byte-identical to base,
  no `tests/` file changed, `tests/` declares ≥ 6 `def test_`, `/health`
  returns `{"status":"ok"}`, `Chart.yaml` version valid semver); compose-check
  byte-identical. Static integrity probes (all pass): (1) tree `scripts/`
  byte-identical to base; (2) `_assert_frozen_subtrees` raises `SystemExit` on
  a tampered `scripts/ci.sh` and passes a clean tree; (3) the copied `lib.sh`
  resolves `REPO_ROOT` to the ephemeral tree, not `/c/micro`; (4) the broken
  tree makes the real tree-local `scripts/ci.sh` fail **specifically** at
  `test_health_returns_ok` (not at build/lint); (5) the golden tree makes the
  same `scripts/ci.sh` exit 0. Post-implementation full regression on the
  committed harness: `A1–A14` (SCORE 100) **and** scenario-001…008
  (broken 10/10/10/10/50/55/10/65, golden 100, compose) **and** scenario-009
  (broken 50 / golden 100 / compose) all PASS — proving the `_TREE_PATHS`
  expansion changes no prior scenario's behaviour. Teardown + integrity clean.
  Frozen-base rule respected — `harness/evaluate.py` append-only,
  `harness/scenario.py` gains the guard + anti-cheat rule + two `_TREE_PATHS`
  entries; no `app/` · `charts/` · `docker/` · `requirements.txt` ·
  `scripts/` · `config/` · `Makefile` · `tests/` file modified.
- **M11 / scenario-010 ✅ complete** — commit `eda6087` (branch
  `continued-development`) *feat: add deterministic unresolved merge conflict
  scenario*. Pre-implementation gate on `c2c9915`: `A1–A14` (`make e2e-base`,
  SCORE 100) **and** scenario-001…009 (broken/golden/compose) all PASS. Broken
  run twice → deterministic **SCORE 0** — `break.patch` commits raw Git conflict
  markers into `requirements.txt`; `pip install` aborts
  (`ERROR: Invalid requirement: '<<<<<<< HEAD'`), `docker build` exits non-zero,
  the image is never produced, deploy is skipped. The completed
  `_finish_build_failure` path (PLAN §11.4) runs the two cluster-free checks —
  `image_build_ok` FAIL (`meta['build_ok']` False) and `git_tree_resolved` FAIL
  — and writes a real `checks.json` + `verdict.json` with the normal path's
  contract semantics: `score` 0, `evidence` `build_log_contains ['<<<<<<<']`
  present, `conflict_marker_files == ['requirements.txt']`, `expectation_problems`
  `[]`, `matches_expectation` True, universal §7.2 anti-cheat clean;
  `enforce=True` would `SystemExit` on any mismatch. Golden → **SCORE 100** via
  the normal build + deploy path, all nine checks PASS (`image_build_ok`:
  `docker build exit 0`; `git_tree_resolved`: `clean`). Anti-cheat clean (base
  §7.2 + golden-only `merge_resolved_cleanly` — no marker line in the tree,
  `requirements.txt` keeps `fastapi` + `uvicorn` with version constraints,
  `docker/Dockerfile` still pip-installs from `requirements.txt`); compose-check
  byte-identical. Static integrity probes (all pass): the broken tree's
  `docker build` genuinely raises and `build.log` names the `requirements.txt`
  conflict as the cause (not an unrelated Docker/pip error); the golden tree's
  `requirements.txt` **and** `docker/Dockerfile` are byte-identical to base;
  the marker-category scan keeps `scenario-010/break.patch` as the sanctioned
  fault fixture while every implementation / golden / doc file stays
  marker-free (`git diff --check` clean). Post-implementation full regression on
  the committed harness: `A1–A14` (SCORE 100) **and** scenario-001…009
  (broken 10/10/10/10/50/55/10/65/50, golden 100, compose) **and**
  scenario-010 (broken 0 / golden 100 / compose) all PASS. Teardown + integrity
  clean. Frozen-base rule respected — `harness/evaluate.py` append-only,
  `harness/scenario.py`'s only non-append change is the `_finish_build_failure`
  build-failure path (explicitly designated by §11.4) plus the new anti-cheat
  rule; no `app/` · `charts/` · `docker/` · `requirements.txt` · `scripts/` ·
  `config/` · `Makefile` · `tests/` file modified.
- **M-BE → M11 sequence complete on `continued-development`** (10 scenarios,
  all deterministic broken/golden/compose, full regression green). `master`
  remains at the submission tag `fc8f7e8`; the continued work lives on
  `continued-development` (`eda6087` + its PLAN commit).
- **Repair agents — deriving `advanced` + broadened `baseline` ✅** — commit
  `4a4bc3e` (branch `continued-development`) *feat: derived advanced repair
  agent + broadened baseline + eval matrix*.
  - **Architecture.** `baseline` stays an offline, no-LLM heuristic (literal
    substitution table + mechanical conflict resolver): solves 001/002/003/007/
    008/009/010, leaves 004/005/006 at broken level (documented boundary).
    `advanced` is now a **deriving fixer** — `_derive_repair` runs 10
    fault-class detectors over the broken tree's own source + collected runtime
    evidence (`events`/`pods.json`/`logs`/`build.log`/`ci.log`) and constructs
    the repair; it never reads `golden.patch`, the golden variant, or expected
    file content on the derivation path. Golden replay survives **only** as an
    explicit fallback: derive → run as `advanced` → validate (SCORE 100 +
    anti-cheat clean) → replay `golden.patch` only on failure. Every advanced
    result records `advanced_provenance.json` (`repair_mode` ∈ derived /
    golden_fallback / no_change / failed, plus derived_attempted /
    derived_validation_passed / fallback_used / final_score / files_modified).
  - **Boundary probes.** Static: `_derive_repair` + all detectors + helpers
    contain no `golden` reference; `run()` reads `golden.patch` exactly once, in
    the labelled fallback branch. Runtime: derivation for all 10 scenarios
    completes with every `golden` path guarded to raise — none touched — and a
    live `advanced` run of scenario-005 with `golden.patch` physically removed
    still returns `repair_mode: derived`, SCORE 100.
  - **Evaluation.** `harness agent-matrix` / `make agents-matrix` runs
    broken/golden/baseline/advanced for scenario-001…010 →
    `.state/agents/matrix.json` + a table. Result (deterministic across two
    independent full runs): **advanced 10/10 `derived`, 0 `golden_fallback`**,
    every fix SCORE 100 and anti-cheat clean; **baseline 7/10** (004/005/006
    `no_change`, score == broken).
  - **Regression.** `make e2e-base` (A1–A14, SCORE 100) + scenario-001…010
    broken/golden/compose + `baseline` 001–010 + `advanced` 001–010 all PASS;
    teardown + integrity clean; `master` unchanged at `fc8f7e8`.
  - **Limitations.** Detectors are per-fault-class; a fault outside the ten
    classes falls through to a visible `golden_fallback`. The derived
    security-context / image-ref repairs apply a standard hardened / canonical
    form (coincides with `golden`, constructed from K8s/Helm knowledge).
- **Improvement 1 / Foundation ✅** — commit
  `d786ace5323b8774fbf2552acb0a29b7c30ba10c` (branch `continued-development`)
  *refactor: split evaluate/scenario into checks/patching/anticheat packages +
  declarative anti-cheat*.
  - **Refactor.** `harness/evaluate.py` **724 → 72 lines** — check
    implementations moved verbatim into the `harness/checks/` package
    (`_util` / `backbone` / `security` / `config` / `observability` / `cicd` /
    `build`); check names, weights, scoring semantics and the registry contract
    unchanged; `evaluate.py` keeps `evaluate()` + `is_healthy()` and re-exports
    moved names so `harness.evaluate` stays a stable import point.
    `harness/scenario.py` **699 → 401 lines** — `harness/patching.py` (tree
    copy, patch apply, frozen-subtree guard, `tree_matches_base` behind
    compose-check) and `harness/anticheat.py` (`universal_anticheat` + a
    declarative, **fail-closed** rule registry: an `evaluation.anticheat` key
    that is not a registered rule raises `ValueError`; `_RULE_ORDER` keeps the
    9 rules' fixed order). `harness/run.py` byte-unchanged.
  - **Fast suite.** **147 new deterministic fast tests** in `tests_meta/`
    (154 with the app's own `tests/`), ~4 s, requiring **no Docker / kind /
    Kubernetes / network / system `patch` executable** (pure-Python unified-diff
    applier `tests_meta/_diffapply.py`, validated to round-trip all 10 real
    scenario patch pairs). Kept outside `tests/` deliberately: `_copy_base_tree`
    sweeps all of `tests/` into every scenario tree, and scenario-009's
    `ci_gate_pass` runs the tree's own pytest.
  - **CI / dev loop.** `.github/workflows/ci.yml` (push/PR: ruff + fast tests +
    helm lint + docker build; no kind, no secrets) and `e2e.yml`
    (`workflow_dispatch`-only full regression); Ruff `F`/`B`/`UP` with three
    pre-existing out-of-scope violations pinned via per-file-ignores (no mass
    edit); `make test-fast` / `lint-py` / `compose-all` / `quick`; existing
    public Make targets unchanged.
  - **Validation.** Fresh pre-refactor runtime baseline captured before any
    code change (scenario-001/006/008/010 broken+golden); Phase 4 compare on
    the refactored harness = **zero graded-field differences** across all
    eight reference runs. Phase 5 full regression green: `make e2e-base` +
    scenario-001…010 broken/golden/compose + agent matrix — **broken scores
    unchanged** (10/10/10/10/50/55/10/65/50/0), **golden all 100**, compose all
    byte-identical, **baseline 7/10**, **advanced 10/10 `derived`,
    `golden_fallback` 0**, provenance schema unchanged. Teardown + integrity
    clean; `master` unchanged at
    `fc8f7e8c8fb832c392dd40e625ce2ffc53519ff9`;
    `pipelinefixrl-submission.zip` untouched. Module map + extension guide:
    `docs/ARCHITECTURE.md`.
- **Improvement 2 / Agent generalization + held-out first-shot benchmark ✅** —
  commits `8ccbe0d62df1c336e2384d45486db52194630892` (**agent freeze**) and
  `bd7fa2938d70c998268137dc136441302cd6028b` (held-out benchmark + official
  evidence), branch `continued-development`.
  - **Architecture (Part A, before any held-out material existed).** The
    advanced agent's 10 scenario-shaped first-match detectors were replaced by
    a **primitive-based repair architecture**
    (`harness/agents/primitives.py`): an Evidence signal layer (typed
    workload-state signals from the run's own artifacts, incl.
    `unschedulable`), a `Finding` model, and six relationship primitives
    (source integrity, chart value wiring, HTTP contract incl. probe *port*,
    Service wiring incl. targetPort↔containerPort, runtime constraints incl.
    Unschedulable clamp, config contract) composed **deterministically**
    (fixed order, edits composed against current candidate bytes, duplicate
    edits collapsed, same-region conflicts recorded in provenance and skipped,
    never silently overwritten). Repair is an iterative
    **derive → apply → validate → observe → refine** loop,
    `MAX_DERIVED_ROUNDS = 3`, per-round provenance (evidence sources,
    findings, edit conflicts, rationale, files, validation score/passed,
    `first_derived_score` vs `final_derived_score`), with the explicit golden
    replay only after every derived round is exhausted and only when
    `allow_golden_fallback=True` (False = generalization mode). All nine
    pre-existing provenance fields kept name and meaning.
  - **Agent freeze methodology.** Fresh pre-refactor baseline captured first
    (baseline 7/10, advanced 10/10 derived, golden_fallback 0 re-confirmed at
    runtime); full 001–010 regression green on the refactored agent (broken
    scores unchanged, golden all 100, all round-1); then the freeze commit
    `8ccbe0d`. From that point `harness/agents/**` was **never modified** —
    verified via `git diff 8ccbe0d… -- harness/agents` (empty) before/after
    every Part B phase and enforced by a fast test; a static scan proves no
    held-out id appears in the frozen implementation.
  - **Held-out benchmark (Part B, authored only after the freeze).**
    `harness/scenarios/held-out/h01|h02|h03` with honest novelty classes:
    h01 (**A**) targetPort 9090 vs containerPort 8000; h02 (**A**)
    request+limit 64Gi → deterministic FailedScheduling (runtime precondition
    recorded: 64Gi > node allocatable 16185008Ki; a first authoring attempt
    with request-only 64Gi was rejected by API-server validation and corrected
    at the definition level); h03 (**B**, genuinely novel) service.port 8081
    vs the published SVC_PORT contract 80. Evaluation-side additions only:
    `service_ports_wired` check (clause A published port / clause B
    targetPort↔containerPort), `service_ports_intact` fail-closed anti-cheat
    rule (`_RULE_ORDER` → 10), `_scenario_dir` held-out resolution,
    `harness agent-generalization` + `make generalization`, golden-access
    guard, create-once evidence artifact writer. Fast suite 179 → **229**.
  - **Official first-shot result (one run; order per scenario: broken →
    frozen advanced with fallback disabled under the golden-access guard →
    archive → only then golden validation).** h01 **100 derived** (round 1),
    h02 **100 derived** (round 1), h03 **65 `no_change`** — the frozen agent
    produced no repair for the Type B relationship and the failure is
    **preserved without tuning**. Aggregate: **2/3 derived, Type A 2/2,
    Type B 0/1, golden_fallback 0**. Artifact
    `docs/evidence/generalization-first-shot.json`, SHA256
    `3cd075bb544c499523fa11aa7694f22aedbf9ca5fbf413ef131093e59ec553cf`,
    **byte-identical after** the post-archive golden validation (h01/h02/h03
    golden all 100, anti-cheat clean, expectation MATCH, compose identity).
  - **Two benchmarks, deliberately separate.** Original development benchmark:
    scenario-001…010, baseline 7/10, advanced 10/10 derived, golden_fallback 0
    (performance on faults the agent was developed against). Held-out
    first-shot: 2/3 derived (frozen-agent generalization probe). The numbers
    are never merged. Methodology, leakage controls, h03 failure analysis and
    limitations: `docs/GENERALIZATION.md`.
- **v2 / Consumer-contract reasoning + second held-out benchmark ✅** — branch
  `v2-generalization`, commits `4d538b74f31e1a13291a61e6cee5744c516183c9`
  (**v2 agent freeze**), `ea980e319ed3b4ab3dc48934d7406cdc4da8e474` (locked
  held-out benchmark) and `cf03bcd9f2d66d45183ff0254f65718524c33e9c` (official
  evidence). All v1 entries above stand unchanged.
  - **Architecture (Option B on a minimal typed contract-data-model seed).**
    `harness/agents/contracts.py` adds endpoint-contract reasoning:
    runtime/tree evidence → typed fact extraction → `Declaration` /
    `Expectation` → deterministic `reconcile()` → `Reconciliation` → `Finding`
    → existing edit composition → validation → iterative observe/refine loop.
    Evidence **parsers extract facts only** (test-enforced: no parser
    constructs a `Reconciliation`); every repair decision lives in the single
    generic reconciliation step; ambiguous, insufficient or conflicting
    evidence, duplicate evidence, unresolved resource identity and
    tree-corroborated current values all mean **no change**. Extraction
    patterns are anchored to documented Kubernetes/kubectl error formats,
    never to benchmark output. Primitives 6 → 7 (`consumer_contract`).
  - **h03 reclassified.** v1's Type B failure is repaired in v2 **by the
    general relationship**, with no scenario id, path match or hard-coded value
    in agent code. h03 (with h01/h02) is therefore a **known
    development/regression case** in v2 — **not** held-out evidence. v1's
    official 2/3 record and its artifact
    (`3cd075bb544c499523fa11aa7694f22aedbf9ca5fbf413ef131093e59ec553cf`) are
    unchanged and hash-pinned by a fast test.
  - **v2 development/regression matrix = 13/13 derived** (scenario-001…010 +
    h01/h02/h03), golden_fallback 0, baseline 7/10, every original broken score
    identical to the pre-v2 baseline, all repairs in round 1. **This is
    regression performance on faults the agent was developed against — it is
    NOT held-out generalization evidence.**
  - **V2 agent freeze.** Established at `4d538b7` after the regression matrix
    passed. `harness/agents/**` stayed byte-identical to it through held-out
    authoring, broken-contract validation, the official first-shot and
    post-archive golden validation — verified at every gate and enforced by a
    fast test.
  - **Held-out benchmark authored after the freeze** —
    `harness/scenarios/held-out-v2/vh01…vh08`, composition **3 Type A / 3 Type
    B / 2 Compound**, classified by inspecting the frozen implementation rather
    than by predicting outcomes. Benchmark-side additions only: generic
    `pod_security_baseline` and `workload_capacity` checks; generic
    `probe_contract_intact`, `min_replicas` and `frozen_paths_intact`
    anti-cheat rules (`_RULE_ORDER` 10 → 13; checks 13 → 15); a `held-out-v2`
    loader root; and a generic scenario-declared `settle_seconds` observation
    window (default 0). Two authoring defects were caught by inspection and
    corrected before locking: vh04 originally mutated `containerPort`, which is
    informational in Kubernetes and produced no runtime symptom (it now moves
    the process's actual listener while the chart stays self-consistent), and
    vh01 needed the settle window because the rollout reports success ~20 s
    before the liveness probe kills the container. All eight broken contracts
    were validated at runtime **before any agent run**; golden patches were
    validated statically only; 32 scenario hashes were locked.
  - **Official first-shot (one run, fallback disabled, golden-access guard
    armed, archived before any golden run): 5/8 derived** — **Type A 3/3**,
    **Type B 0/3**, **Compound 2/2**, **golden_fallback 0**, **no_change 3**,
    **exceptions 0**. vh04/vh05/vh06 matched no primitive at all and produced
    `no_change` with zero derived rounds: the frozen agent has no reasoning
    about the port the application actually binds, the pod-level security
    context, or workload capacity. **No post-result tuning occurred.**
  - **Composition / iteration, observed in production.** vh07 repaired **two
    independent findings in ONE round** (first runtime evidence that
    same-candidate composition works outside the fast tests). vh08 required
    **two observed rounds**: round 1 resolved the build-blocking conflict and
    scored **75**; only then did the published-port fault become observable as
    consumer evidence, and round 2 reached **100**. Two rounds were observed,
    not forced by design, and `first_derived_score = 75` is preserved.
  - **Post-archive golden validation: 8/8 at score 100**, every expectation
    MATCH, anti-cheat clean, every `break + golden` composition byte-identical
    to base — no benchmark-methodology defect. This is what licenses reading
    the three Type B results as genuine frozen-agent capability boundaries
    rather than invalid scenarios.
  - **Evidence.** `docs/evidence/generalization-first-shot-v2.json`, SHA256
    `aeb57f0d7aa459fc568f99cafd84d1c1d84db08573a934ae5d94abb74230c4c2`,
    generated exactly once (writer refuses overwrite) and byte-identical before
    and after golden validation; the official first-shot was never rerun. Fast
    suite 337 tests. Methodology, taxonomy and limitations:
    `docs/GENERALIZATION.md`.
  - **Trajectory, stated honestly.** v1: development 10/10 derived, official
    held-out **2/3**. v2: development/regression **13/13** derived, official
    held-out **5/8** on a larger and harder benchmark. The improvement reflects
    one closed capability gap (consumer-side contract reasoning), not general
    repair ability; no universal-generalization claim is made.

| Milestone | Scenario | Base change? | Regression gate before it | Gate ≈ | Scenario suite ≈ |
|-----------|----------|--------------|---------------------------|-------:|-----------------:|
| M-BE ✅ | base-evolution | **yes (only here)** | — (its own gate: A1–A14 + 001) | ~8 min | ~10 min (build+tests) |
| M3 ✅ | 002 wrong pinned tag | no | A1–A14 + 001 | ~8 | ~3 |
| M4 ✅ | 003 OOMKilled | no | + 002 | ~11 | ~4 |
| M5 ✅ | 004 template wiring | no | + 003 | ~15 | ~3.5 |
| M6 ✅ | 005 Service selector | no | + 004 | ~18 | ~3 |
| M7 ✅ | 006 runs-as-root | no | + 005 | ~21 | ~3 |
| M8 ✅ | 007 ConfigMap ref | no | + 006 | ~24 | ~3.5 |
| M9 ✅ | 008 log-format regression | no | + 007 | ~28 | ~3 |
| M10 ✅ | 009 CI + health contract | no | + 008 | ~31 | ~5 |
| M11 ✅ | 010 merge conflict | no (harness only) | + 009 | ~36 | ~4 |

- **Per-scenario suites (002–010):** ≈ **32 min** total.
- **Regression gates (M3–M11):** ≈ **182 min** total (each = `A1–A14` ~5 min +
  every prior scenario's broken/golden/compose suite).
- **M-BE:** ≈ **18 min**. **kind up/down** across sessions ≈ **6 min**.
- **Full M-BE → M11 automated run time ≈ 4.0–4.3 hours** (warm Docker cache, one
  shared cluster per session; excludes authoring / debugging). This is
  ~neutral vs. threading the two base deltas through M8/M10 (~4.0 hr there too):
  the win is **structural, not temporal** — one tested base change instead of
  two mid-program, zero patch-drift surface across M3–M11, and every regression
  gate becomes a pure "frozen base + prior scenarios still green" check.
