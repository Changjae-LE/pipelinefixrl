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
   `readinessProbe.httpGet.path: /health` → `/healthz`.
4. `golden.patch` — unified diff reverting `/healthz` → `/health` (applied on the
   broken tree, or an equivalent absolute patch on the base tree).
5. Harness scenario support:
   - `harness/run.py` `run_scenario(scenario_id, variant)`:
     copy base tree to `.state/runs/<run-id>/tree/` → `git apply` the selected
     patch(es) → build/load/deploy from that tree → collect → evaluate against
     `scenario.yaml.expect.<variant>`.
   - Anti-cheat diff check (SPEC §7.2) wired in; golden must not trip it.
   - `evaluate.py` gains `--expect` handling: compares actual checks to
     `must_fail` / `must_pass` / `score_min` / `score_max`.
6. Make targets: `scenario-001-broken`, `scenario-001-golden`,
   `scenario-001` (runs both, asserts expectations), and `eval` (runs all
   scenarios then `kind-down`).
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
  `404`; pod logs show `GET /healthz` 404 lines. Artifacts non-empty as in A10.
- **B4 — golden full score.** `make scenario-001-golden`: all weighted checks
  `PASS`; `report.txt` `SCORE: 100`; matches
  `scenario.yaml.expect.golden.must_pass` and `score_min: 100`; target exits `0`.
- **B5 — golden is a minimal, honest fix.** `golden.patch` touches only
  `charts/app/values.yaml` and only the `readinessProbe.httpGet.path` line
  (verified by `git apply --stat` / diff line count == 1 changed hunk). Anti-cheat
  check reports no violation.
- **B6 — break/golden compose.** Applying `break.patch` then `golden.patch` to
  the base tree yields a tree byte-identical to base (`git diff` empty).
- **B7 — comparison output.** `make scenario-001` runs broken then golden,
  writes a `.state/runs/` comparison line/table showing
  `broken: SCORE <=60 (expected FAIL set matched)` vs
  `golden: SCORE 100`, and exits `0` only if **both** match their expectations.
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

1. **M0** bootstrap + finalize version pins (needs: approval + host tools
   installed + Docker running).
2. **M1** app → tests → Docker → chart → kind scripts → harness deploy/verify →
   Makefile. **Stop. Demonstrate A1–A14.**
3. **M2** scenario-001 broken + golden. **Stop. Demonstrate B1–B10.**
4. Only after M1 **and** M2 pass end to end: design scenarios 002–010 (separate
   plan revision).

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

## 10. Approval gate

Implementation of M0/M1 has **not** started. Awaiting explicit "approved / start"
before creating any application, chart, script, or harness code.
