# PipelineFixRL — module architecture

## Data flow

```
scenario.yaml + break.patch/golden.patch
        │
        ▼
harness.patching        copy base tree ─► apply break (+ golden / agent patch) ─► frozen-subtree guard
        │
        ▼
harness.anticheat       universal §7.2  +  scenario-declared rules (registry, fail-closed)
        │
        ▼
harness.run             docker build ─► kind load ─► helm deploy ─► rollout wait
        │
        ▼
harness.collect         kubectl get events/pods/rollout/svc/endpoints + pod logs  ─► .state/runs/<id>/
        │
        ▼
harness.evaluate        run_backbone_checks()  +  activated scenario checks  ─► weighted score
   └─ harness.checks/    backbone · security · config · observability · cicd · build   (+ _util plumbing)
        │
        ▼
harness.scenario        _evidence_scan · _check_expect ─► verdict.json / checks.json / report.txt
```

## Package map

| module | responsibility |
|---|---|
| `harness/patching.py` | ephemeral-tree copy (`_copy_base_tree`), unified-diff apply (`_apply_patch`), the `scripts/`+`config/` frozen-subtree integrity guard (`_assert_frozen_subtrees`), tree-vs-base byte diff (`tree_matches_base`, behind `harness compose-check`) |
| `harness/anticheat.py` | `universal_anticheat(tree)` (PROJECT_SPEC §7.2, runs for both variants); a **declarative registry** — `@register_anticheat_rule("<name>")` + `run_scenario_anticheat(cfg, tree, loaded_tags)`. Fail-closed: an `evaluation.anticheat` key that is not a registered rule raises `ValueError`. Nine rules, run in a fixed `_RULE_ORDER`. |
| `harness/checks/__init__.py` | the scenario-check registry (`_SCENARIO_CHECKS`, `register_scenario_check`); imports the six domain modules to trigger registration; re-exports helper names |
| `harness/checks/_util.py` | shared plumbing: artifact IO (`_load`), port-forward HTTP probes (`_http_health`, `_http_get_json`, `_burst_health`, `measure_stdout_lines`), stdout-log parsing (`_log_lines`, `_parse_json_logs`), conflict-marker scan (`_conflict_marker_files`) |
| `harness/checks/backbone.py` | `run_backbone_checks()` — the seven always-on rows (helm / rollout / deployment / pods / endpoints / http-health / no-bad-events); `CHECK_WEIGHTS`; the `service_selects_pods` scenario check |
| `harness/checks/security.py` | `runs_as_nonroot`, `readonly_rootfs`, `no_priv_escalation`, `caps_dropped` (applied `pods.json` securityContext) |
| `harness/checks/config.py` | `config_applied` (rendered `configMapKeyRef` + live `GET /` tier) |
| `harness/checks/observability.py` | `structured_logs_ok` + `_logs_are_json` / `_stdout_line_count` / `_base_stdout_line_count` |
| `harness/checks/cicd.py` | `ci_gate_pass` (runs the real `scripts/ci.sh` from inside the tree) |
| `harness/checks/build.py` | `image_pull_ok`, `no_oomkill`, `image_build_ok`, `git_tree_resolved` |
| `harness/evaluate.py` | orchestration only: `evaluate()` (backbone + activated scenario checks + weight re-balance + score + `checks.json`), `is_healthy()`, and back-compat re-exports so `harness.evaluate` stays a stable import point |
| `harness/scenario.py` | `run_scenario()` orchestration, `_finish_build_failure` (the "image never built" path), `_evidence_scan`, `_check_expect`, `compose_check`, `cleanup_scenario_ns` |
| `harness/agents/primitives.py` | the advanced agent's repair layer: **Evidence** (typed workload-state signals parsed from a run's own artifacts — events/pods/logs/build/CI + failed-check reasons), the **Finding** model (primitive · diagnosis · rationale · edits), seven relationship **primitives** (source integrity, chart value wiring, HTTP contract incl. probe port, Service wiring incl. targetPort↔containerPort, runtime constraints incl. Unschedulable clamp, config contract, and — v2 — consumer contract), and the deterministic **composition engine** (fixed order, edits composed against current candidate bytes, duplicates collapsed, same-region conflicts recorded + skipped) |
| `harness/agents/contracts.py` | **v2 endpoint-contract reasoning**: typed `Declaration` (the tree provides an endpoint attribute) / `Expectation` (a consumer named one) / `Reconciliation` (a decided repair). Evidence **parsers extract facts only**; the single `reconcile()` is the only place a repair is decided. Extraction patterns are anchored to documented Kubernetes/kubectl error formats, never to benchmark output |
| `harness/agents/fix_agent.py` | `baseline` (offline heuristic) + `advanced`: the iterative **derive → apply → validate → observe → refine** loop over the primitives (`MAX_DERIVED_ROUNDS = 3`, per-round provenance incl. `first_derived_score` vs `final_derived_score`); the explicit golden replay runs only after all derived rounds are exhausted **and** only when `allow_golden_fallback=True` — that flag is the fallback boundary the generalization benchmark disables; `harness agent` / `harness agent-matrix` |
| `harness/generalization.py` | held-out first-shot evaluation (benchmark side): runs the **frozen** agent (`AGENT_FREEZE_COMMIT`) with the fallback disabled under a **golden-access guard** (any Python-side read of a `golden` path raises), archives the create-once evidence artifact, then post-archive golden validation; `harness agent-generalization` / `make generalization` |

## Advanced-agent repair loop

```
broken run artifacts ──► Evidence (typed signals + failed-check reasons)
        │
        ▼                       round 1..MAX_DERIVED_ROUNDS(3)
candidate tree ─► primitives (fixed order) ─► Findings ─► deterministic compose
        │            (duplicates collapsed; same-region conflicts recorded+skipped)
        ▼
patch vs broken tree ─► run as `advanced` ─► validate: SCORE 100 + anti-cheat
        │                                     clean + expectation
        ├─ pass ──► repair_mode=derived  (fallback_used=false)
        └─ fail ──► observe THIS run's evidence ─► refine (next round)
                         │ (rounds exhausted)
                         ├─ allow_golden_fallback=True  ─► explicit golden replay
                         │                                 (repair_mode=golden_fallback)
                         └─ False (generalization mode) ─► failed / no_change
```

Every round is recorded in `advanced_provenance.json` (evidence sources,
findings, edit conflicts, rationale, files, validation score/passed); the
derivation path never reads golden material — enforced by static tokenize
scans, runtime path-poisoning tests, and the generalization runner's guard.
**Frozen-agent boundary:** each held-out evaluation pins `harness/agents/**`
byte-identical to its freeze commit — `8ccbe0d…` for v1, `4d538b7…` for v2 —
with all held-out work living entirely on the evaluation side; see
`docs/GENERALIZATION.md`.

## Consumer-contract reasoning (v2)

Most primitives reconcile two *declarations* inside the tree (X in file A vs Y
in file B). Some expectations, though, live only outside it: a consumer failure
that names the endpoint it tried to reach. v2 makes those first-class.

```
run's own evidence (failed-check reasons, events, logs)
        │
        ▼  typed fact extraction — parsers only, no decisions
   Expectation{kind, name, attribute, value, authoritative}
                    +
   Declaration{kind, attribute, value, source}   ◄── the candidate tree
        │
        ▼  deterministic reconciliation — the ONLY decision point
   Reconciliation{current -> expected}   ──►  Finding
        │
        ▼
   edit composition ─► validation ─► iterative observe/refine loop
```

Reconciliation refuses to act unless the evidence is unambiguous. **No change**
is returned when: the evidence names no resource/value (ambiguous); there is no
declaration, or it already satisfies the contract (insufficient); two equally
attested expectations disagree (conflicting); the resource identity cannot be
resolved; or the current value has independent tree-side corroboration.
Duplicate evidence never amplifies attestation — attestation is the set of
distinct expected values, never an occurrence count.

This is why h03 (v1's held-out failure) is repaired in v2 **by the general
relationship, not by a scenario-specific branch**: a consumer error naming the
port it expected is reconciled against the tree's declaration. No agent module
contains a scenario id, a held-out id, or the faulty value — enforced by static
tests.

**Why the iterative loop matters — observed, not hypothetical.** In the v2
held-out run, vh08 carried a build-blocking dependency conflict *and* a
published-port regression. Round 1 could only see a build failure, so it
resolved the conflict and scored 75; the port fault became observable as
consumer evidence only once the image built and deployed, and round 2 repaired
it to 100. A single-shot design would have stopped at 75.

**Represented vs absent relationships.** The primitives cover a fixed, reusable
family of relationships. Faults outside that family produce an honest
`no_change` rather than a guess — in the v2 held-out benchmark, vh04 (the port
the application process actually binds), vh05 (pod-level security context) and
vh06 (workload replica capacity) each matched no primitive at all. That
boundary is real and documented, not a defect.

## Extending

**Add a scenario check** — write `@register_scenario_check("my_check", 15)` in the
matching `harness/checks/<domain>.py`, list it under `evaluation.checks` in the
scenario's `scenario.yaml`, and re-balance `evaluation.weights` so the total is
100. `evaluate()` needs no change.

**Add an anti-cheat rule** — write `@register_anticheat_rule("my_rule")` in
`harness/anticheat.py` (signature `(*, rules, tree, tv, loaded_tags, repo_root)
-> list[str]`), append its name to `_RULE_ORDER`, and reference it under
`evaluation.anticheat` in the owning scenario's `scenario.yaml`. Unknown names
are rejected at run time.

**Add a scenario** — see the six-file recipe in `docs/PLAN.md` §11 (scenario.yaml,
task.md, break.patch, golden.patch, plus any check / anti-cheat rule).

## Tests & CI

| command | what | needs |
|---|---|---|
| `make quick` | `lint` (helm) + `lint-py` (ruff) + `test-fast` (pytest) + `compose-all` | nothing beyond the venv + helm |
| `make test-fast` | `tests/` (app) + `tests_meta/` (harness/agent meta suite) — ~230 fast unit tests, no Docker/kind/K8s/network/`patch` | venv |
| `make generalization` | held-out first-shot: frozen advanced agent, golden fallback disabled, golden-access guard (`docs/GENERALIZATION.md`) | Docker + kind |
| `make lint-py` | `ruff check harness app tests tests_meta` (`F`/`B`/`UP`) | venv |
| `.github/workflows/ci.yml` | ruff + fast tests + `helm lint` + `docker build`, on every push/PR | — |
| `.github/workflows/e2e.yml` | `make e2e-base` + `scenario-001..010` + `agents-matrix`, `workflow_dispatch` only | GitHub runner |
| `make e2e-base`, `make scenario-00N`, `make agents-matrix` | the authoritative full regression | Docker + kind |
