# Milestone 2 — Acceptance Evidence (scenario-001)

Captured: 2026-08-29. Host: Windows 11, Docker Desktop, Git Bash, cp949 locale.
kind `v0.33.0`, node `kindest/node:v1.37.0@sha256:a1ed56cf…`.

**Scenario:** `harness/scenarios/scenario-001/` — *incorrect readiness probe
path*. `break.patch` changes `charts/app/values.yaml`
`readinessProbe.httpGet.path` from `/health` to `/health2` (a route the app does
not serve). The liveness probe is left on `/health`, so pods are never restarted
— they just never become Ready. `golden.patch` reverts that one line.

Runs under test:
- broken: `scenario-001-broken-20260829T020658Z`
- golden: `scenario-001-golden-20260829T020949Z`

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| B1 | Broken deploys but fails readiness: namespace created; `helm_release_ok=PASS`; `rollout_complete=FAIL` (timeout); `deployment_ready=FAIL` (`readyReplicas` 0/null); `endpoints_present=FAIL`; `http_health_ok=FAIL` | **PASS** | namespace `pfrl-scenario-001-broken-20260829t020658z` created; `helm status` = `deployed`. `checks.json`: `helm_release_ok PASS`; `rollout_complete FAIL kubectl rollout status failed/timed out`; `deployment_ready FAIL spec=1 ready=0 updated=1 available=0`; `pods_ready FAIL all_ready=False restarts=0`; `endpoints_present FAIL ready endpoint addresses=0`; `http_health_ok FAIL skipped probe: service has no ready endpoints`. Deployed manifest (`rollout.json`) `readinessProbe.httpGet.path = /health2`, `livenessProbe.httpGet.path = /health`, `status.readyReplicas = None` |
| B2 | Broken `SCORE ≤ 60` and ≤ `scenario.yaml expect.broken.score_max`; target exits 0 because the failure **matches** the expectation | **PASS** | `report.txt` `SCORE: 10`; `scenario.yaml` `score_max: 60`. `verdict.json` `matches_expectation: true`, `expectation_problems: []`. `BROKEN HARNESS EXIT: 0` (all `must_fail` FAILed, `must_pass:[helm_release_ok]` PASSed). Run as a "must be healthy" check it would exit non-zero — the `scenario` command exits 0 only on expectation match |
| B3 | Broken evidence: ≥1 `Warning Unhealthy` event citing the readiness probe + HTTP `404`; pod logs show `GET /health2` 404 lines; artifacts non-empty | **PASS** | `events.json`: `count=11 type=Warning reason=Unhealthy message="Readiness probe failed: HTTP probe failed with statuscode: 404"`. `logs/app-*.log`: 11× `INFO: 10.244.0.1:* - "GET /health2 HTTP/1.1" 404 Not Found`. Evidence scan in `verdict.json`: `events contains 'Unhealthy'`=true, `events contains '404'`=true, `logs contains '/health2'`=true, `logs contains '404'`=true. Run dir: 16 files, **0 empty** |
| B4 | Golden: all weighted checks PASS; `SCORE: 100`; matches `expect.golden.must_pass` + `score_min: 100`; exits 0 | **PASS** | `checks.json`: `helm_release_ok, rollout_complete, deployment_ready, pods_ready, endpoints_present, http_health_ok` all `PASS`; `http_health_ok  GET /health -> 200 {"status":"ok"}`. `report.txt` `SCORE: 100`, `HEALTHY: yes`. `verdict.json` `matches_expectation: true`. `GOLDEN HARNESS EXIT: 0` |
| B5 | Golden is a minimal, honest fix: `golden.patch` touches only `charts/app/values.yaml`, only the `readinessProbe.httpGet.path` line; anti-cheat clean | **PASS** | `git apply --stat golden.patch` → `charts/app/values.yaml \| 2 +-` / `1 file changed, 1 insertion(+), 1 deletion(-)`; single hunk, `-    path: /health2` / `+    path: /health`. `anticheat.json` = `[]` for the golden run; `verdict.json` `anticheat_violations: []` |
| B6 | `break.patch` then `golden.patch` on the base tree → byte-identical to base | **PASS** | `make scenario-001-compose` → `harness compose-check` copies the base tree, applies both patches with `patch -p1`, byte-compares every file under `app/ charts/ docker/ tests/ requirements.txt pyproject.toml .dockerignore`: `compose-check scenario-001: PASS — break.patch + golden.patch == base (byte-identical)`, exit 0. `compose-check.json` `diffs: []` |
| B7 | `make scenario-001` runs broken → golden → compose; each writes `verdict.json` + prints `SCORE` / `MATCH`; aggregate exits 0 only if all match | **PASS** | broken `verdict.json` score=10 match=true; golden `verdict.json` score=100 match=true; compose PASS. Each variant printed `expectation : MATCH`. The three sub-targets each exited 0 |
| B8 | Each variant in its own namespace; both deleted after their runs; cluster deleted at the end; `~/.kube/config` unchanged | **PASS** | namespaces `pfrl-scenario-001-broken-…` and `pfrl-scenario-001-golden-…` (distinct); `harness scenario-cleanup-ns` deleted both → `kubectl get ns` shows no `pfrl-*`; `kind-down` → `Deleted nodes: ["pipelinefixrl-control-plane"]`, `kind get clusters` empty, no kind containers, `.state/kubeconfig` removed. `~/.kube/config` absent at baseline and after (recorded baseline `absent`) — **unchanged** |
| B9 | No `tests/**` changed by either patch; both probes present in the golden deployed manifest | **PASS** | anti-cheat walks every non-transient file under `tests/` and byte-compares — `[]` for both variants. Golden `rollout.json`: `readinessProbe.httpGet.path=/health`, `livenessProbe.httpGet.path=/health`; `securityContext` `runAsNonRoot=True, allowPrivilegeEscalation=False, readOnlyRootFilesystem=True` |
| B10 | Determinism: repeated runs → identical PASS/FAIL sets and scores (tags/run-ids aside) | **PASS (broken)** | Two broken runs the same session — `…020249Z` and `…020658Z` — both `SCORE: 10` with the identical set `helm_release_ok PASS` + `{rollout_complete, deployment_ready, pods_ready, endpoints_present, http_health_ok} FAIL`. (The first also flagged a since-fixed `__pycache__/*.pyc` anti-cheat false positive that does not affect the broken expectation.) Golden determinism follows from the golden tree being byte-identical to base, which M1 A14 already showed reproducible at `SCORE: 100` across repeated runs |

## Image hygiene (constraints check)

- Broken image `pipelinefixrl/app:scenario-001-broken-60a527267ec9-20260829T020658Z`;
  golden `pipelinefixrl/app:scenario-001-golden-60a527267ec9-20260829T020949Z`.
  Unique per build, never `:latest`, `imagePullPolicy: Never`, side-loaded with
  `kind load docker-image`; `meta.json image_on_node: true` for both.
- Each variant built from an isolated copy of the base tree under
  `.state/runs/<run-id>/tree/` with the patch(es) applied there — the repo
  working tree is never mutated by a scenario run.

## Anti-cheat (PROJECT_SPEC 7.2)

`harness/scenario.py:_anticheat` fails a submission if: `readinessProbe` or
`livenessProbe` is dropped from chart values; `securityContext.runAsNonRoot` /
`allowPrivilegeEscalation` / `readOnlyRootFilesystem` is removed or weakened;
`replicaCount` is `0`/absent; or any non-transient file under `tests/` differs
byte-for-byte from base. Result is recorded in `anticheat.json` and folded into
`verdict.json`; a `golden` expectation carries `anticheat_clean: true`.

## Reproduce

```bash
make kind-up
make scenario-001      # broken (score 10) -> golden (score 100) -> compose check
make kind-down
```
