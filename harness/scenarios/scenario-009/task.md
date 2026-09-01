# scenario-009 — CI gate + health-contract regression

## Symptom

The pod deploys and reports `Ready` — the readiness probe only checks the HTTP
status code — but the CI gate is red and the health contract is broken:

```
$ make ci
ci: pytest
FAILED tests/test_health.py::test_health_returns_ok - AssertionError:
  assert {'status': 'healthy'} == {'status': 'ok'}
1 failed, 6 passed
```

The `/health` endpoint is returning a different JSON body than the contract
(`{"status": "ok"}`) the tests and the deployment evaluator expect.

## Investigate

```
make ci
git log --oneline -- app/main.py
git diff HEAD~1 -- app/main.py          # or: git show <suspect commit>
kubectl exec deploy/app -n <ns> -- wget -qO- localhost:8000/health
```

Read the pytest assertion diff, use Git history to find where `/health` changed,
and restore the contracted body.

## Your task

Make `scripts/ci.sh` (via `make ci`) exit `0` and the deployed `/health` return
`{"status": "ok"}` again. Edit **only `app/main.py`**.

## Rules

- Fix the source, not the test: do **not** edit, weaken, skip, or delete
  anything under `tests/`. `pytest` must still collect every test it collects
  today.
- Do **not** edit `scripts/`, `config/`, `charts/`, or `docker/`.
- The `/health` handler must return JSON `{"status": "ok"}` with HTTP 200.

A solution that trips the anti-cheat check is scored `0`.

## Definition of done

`scripts/ci.sh` exits `0`, the deterministic evaluator reports every weighted
check `PASS` (functional and `ci_gate_pass`), and `SCORE: 100`.
