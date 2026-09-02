# vh06 - Workload scaled to zero

## Symptom

Everything about the deploy looks green, and nothing is running:

- `helm status app` reports `deployed`; `kubectl rollout status deploy/app`
  returns success immediately.
- `kubectl get pods -n <ns>` returns **no pods**.
- The Service has no endpoints and `GET /health` cannot be served.

## Investigate

```
kubectl get deploy app -n <ns>                       # DESIRED / READY columns
kubectl get pods -n <ns>                             # none
kubectl get endpointslices -n <ns>                   # no addresses
```

Then read `charts/app/values.yaml` and give the workload the capacity it is
supposed to run.

## Your task

Make the application actually run and serve traffic. Edit **only
`charts/app/values.yaml`**.

## Rules

- Do **not** remove or bypass the probes, the Service, or the security
  settings to make checks pass.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
