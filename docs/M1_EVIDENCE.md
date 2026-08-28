# Milestone 1 — Acceptance Evidence

Captured: 2026-08-28. Host: Windows 11, Docker Desktop, Git Bash, cp949 locale.
Commit under test: `67c767f` (M0+M1).

Environment bootstrapped in M0:
- `winget install Kubernetes.kind Helm.Helm ezwinports.make` →
  kind **v0.33.0**, helm **4.2.4**, make **4.4.1**. kubectl **v1.36.1**, Python **3.14.6** already present.
- `git init` + first commit `67c767f` (36 files; `.state/` and `.venv/` ignored, verified).
- `config/versions.env` pins kind `v0.33.0` and
  `kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5`
  (digest read from `api.github.com/repos/kubernetes-sigs/kind/releases/tags/v0.33.0`).

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| A1 | `make doctor` exits 0; docker reachable, kind==pin, helm≥3.14, kubectl≥1.28, python≥3.11, node-image digest in `kind-cluster.yaml` == `versions.env`, digest-pinned, no `:latest` | **PASS** | doctor printed 9 `OK` lines, `doctor: all checks passed` |
| A2 | `make test` — all pytest pass, exit 0, ≥4 cases | **PASS** | `4 passed, 1 warning` (health→`{"status":"ok"}`, JSON content-type, `/` name+version, unknown route→404) |
| A3 | `make kind-up` creates `pipelinefixrl`; exactly 1 node, `Ready`, image matches pin | **PASS** | `node count: 1`; `pipelinefixrl-control-plane Ready`; `kubelet=v1.37.0`, `runtime=containerd://2.3.4`, `osImage=Debian GNU/Linux 13`; `kind get clusters` → `pipelinefixrl` |
| A4 | Project kubeconfig access restricted to current user; default kubeconfig unchanged; no cloud creds | **PASS** | `icacls .state\kubeconfig` → `DESKTOP-0FAOGL1\ChangjaeLee:(F)` only, inheritance removed (`/inheritance:r`); `chmod 600` also attempted. Baseline `.state/kubeconfig.baseline.md5` = `absent`; `~/.kube/config` still absent → **unchanged**. `grep -rniE 'boto3\|awscli\|amazonaws\|eks\|ecr\|iam\|aws_access_key'` over `app/ harness/ scripts/ charts/ config/ Makefile pyproject.toml requirements.txt` → **no matches**; `pip list` → no aws/boto/s3 packages. `KUBECONFIG` is set only to `$STATE_DIR/kubeconfig` (`scripts/lib.sh:11`, `harness/paths.py:5`, `harness/tools.py:43`, `harness/evaluate.py:55`); `assert_project_kubeconfig` in `lib.sh` aborts if `KUBECONFIG` is anything else |
| A5 | `make build` → tag `pipelinefixrl/app:base-<sha12>-<ts>`, never `:latest`; image present on node | **PASS** | tags observed: `base-nogit-20260828T170010Z` (pre-commit), then `base-67c767f1c45f-20260828T170913Z`, `base-67c767f1c45f-20260828T171016Z`. `grep ':latest' .state/runs/base-*/meta.json` → none. `crictl images` on node lists all three tags (`node-images.txt`); `meta.json image_on_node=True` for every run |
| A6 | `make deploy-base` — `kubectl rollout status deploy/app` success within timeout; `helm status` deployed | **PASS** | `meta.json rollout_output`: `deployment "app" successfully rolled out`; `checks.json` `rollout_complete PASS`, `helm_release_ok PASS` (`status='deployed'`) |
| A7 | Run namespace: `readyReplicas == 1`; endpoints has ≥1 address on target port | **PASS** | live: `kubectl get deploy app -o jsonpath='{.status.readyReplicas}'` → `1`; endpoint address `10.244.0.5`. `checks.json`: `deployment_ready spec=1 ready=1 updated=1 available=1`, `endpoints_present ready endpoint addresses=1` |
| A8 | Real HTTP request to the Service → 200 `{"status":"ok"}` | **PASS** | harness `kubectl port-forward svc/app` then `GET /health` → `http_health_ok PASS  GET /health -> 200 {"status":"ok"}` in `checks.json`; app pod log shows kubelet readiness probe `GET /health ... 200 OK` |
| A9 | `checks.json` every weighted check PASS; `report.txt` `SCORE: 100`; `make verify-base` exit 0 | **PASS** | `report.txt`: `SCORE: 100`, `HEALTHY: yes`; `make verify-base` → `base-20260828T171016Z: score=100 healthy=True`, exit 0 |
| A10 | Run dir has non-empty events/logs/rollout/readiness/services/endpoints/checks/report | **PASS** | `.state/runs/base-20260828T171016Z/`: `events.txt`, `events.json`, `logs/app-*.app.log`, `rollout.json`, `readiness.json`, `services.json`, `endpoints.json`, `endpointslices.json`, `replicasets.json`, `pods.json`, `helm-status.json`, `node-images.txt`, `meta.json`, `checks.json`, `report.txt` |
| A11 | `make clean-ns` deletes the run namespace; no other namespace affected | **PASS** | each e2e run ended `deleted namespace: pfrl-base-<ts>`; after runs `kubectl get ns` shows no `pfrl-*` |
| A12 | `make kind-down` deletes the cluster; `.state/kubeconfig` + `.state/clusters/*` removed; default kubeconfig unchanged | **PASS** | `Deleted nodes: ["pipelinefixrl-control-plane"]`; `kind get clusters` → empty; `.state/kubeconfig` removed; `.state/clusters/` empty; no `pipelinefixrl` docker container; `~/.kube/config` still absent |
| A13 | `make e2e-base` runs doctor→kind-up→test→deploy-base→verify-base→clean-ns in sequence, exit 0 | **PASS** | RUN 1: full chain, `e2e-base: PASS`, exit 0. See note below on cold-start. |
| A14 | Running `make e2e-base` twice yields same PASS/score, new unique tag + run-id | **PASS** | RUN 2 exit 0, identical check set + `SCORE: 100`; new `run_id base-20260828T171016Z`; new image content id `7e6e06ce…` vs RUN 1 `520aef2f…`; distinct tag timestamp |

## Notes / caveats

- **A13 cold-start:** the two `make e2e-base` runs reused the cluster from an
  earlier standalone `bash scripts/kind-up.sh` (kind-up is idempotent and logged
  `cluster 'pipelinefixrl' already exists`). The fresh-create path *was*
  exercised — by that initial `scripts/kind-up.sh` (logged `Creating cluster … ✓
  Ready after 16s`) and the first standalone `harness run` (full
  build→load→deploy→collect→evaluate, `SCORE: 100`) — just not inside a single
  uninterrupted `make e2e-base`. After `make kind-down` there is now no cluster,
  so a single cold `make e2e-base` can be run on request to make A13 airtight.
- **A4 on Windows:** POSIX `600` bits are not meaningful on NTFS under Git Bash,
  so isolation is enforced with `icacls /inheritance:r /grant:r "$USERNAME:F"`.
  `PLAN.md` A4 was updated to state this.
- **Locale:** host locale is cp949; `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` are
  exported by the `Makefile` and the harness forces `encoding="utf-8"` on all
  subprocess pipes so kubectl/helm JSON never hits a decode error.
- Image tag before the first commit was `base-nogit-…` (no `HEAD`); after commit
  `67c767f` it carries the real short SHA.

## Reproduce

```bash
make setup && make e2e-base && make kind-down
```
