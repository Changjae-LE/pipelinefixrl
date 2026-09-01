# scenario-008 — Structured-log format regression

## Symptom

Every Kubernetes signal is green:

```
$ kubectl get deploy app -n <ns>
NAME   READY   UP-TO-DATE   AVAILABLE
app    1/1     1            1
$ kubectl get pods -n <ns>
NAME             READY   STATUS    RESTARTS   AGE
app-…            1/1     Running   0          30s
```

`GET /health` returns `200`. But the logs are no longer machine-parseable:

```
$ kubectl logs deploy/app -n <ns> --tail=5
INFO uvicorn.error: Application startup complete.
INFO uvicorn.access: 10.244.0.1:52111 - "GET /health HTTP/1.1" 200
$ kubectl logs deploy/app -n <ns> | jq .
jq: error (at <stdin>:1): Invalid literal
```

The base app emits **one JSON object per stdout line** (`{ts, level, logger,
msg, …}`, plus `method` / `path` / `status` for HTTP access lines). Right now it
is emitting free text, so log collection / schema validation downstream fails.

## Investigate

```
kubectl logs deploy/app -n <ns> --tail=20
kubectl logs deploy/app -n <ns> | jq .
kubectl exec deploy/app -n <ns> -- printenv LOG_FORMAT
helm get values app -n <ns>
```

Compare the running `LOG_FORMAT` against the chart's default. One configuration
value selects the log encoding; it is currently set to the human-readable mode.

## Your task

Restore machine-parseable structured (JSON) logging **without touching the
application or the logging formatter** — the code is correct, only the
configuration is wrong. Edit **only `charts/app/values.yaml`**.

## Rules

- The deployment must stay fully healthy (every functional check still PASS).
- Do **not** edit `app/`, `charts/app/templates/`, or anything under `tests/`.
- Do **not** raise `logLevel` to silence the access logs, and do **not** pick a
  log-format value that is not a recognised mode.
- The rendered Deployment must still source `LOG_FORMAT` from
  `.Values.logFormat`.

A solution that trips the anti-cheat check is scored `0`.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` (functional and
`structured_logs_ok`) and `SCORE: 100`.
