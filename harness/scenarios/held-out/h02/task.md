# h02 — Resource request no node can satisfy

## Symptom

The release installs but the rollout never completes:

- `kubectl get pods -n <ns>` shows the pod `Pending` with `0` restarts —
  forever. No container ever starts.
- `kubectl describe pod` / events show `FailedScheduling` with
  `Insufficient memory`.
- `kubectl rollout status deploy/app` times out.

## Investigate

```
kubectl get pods -n <ns>                                          # Pending, 0/1
kubectl get events -n <ns> | grep -i sched                        # FailedScheduling
kubectl get pod -n <ns> -o jsonpath='{..resources.requests}'      # what was asked for
kubectl describe node                                             # what the node can allocate
```

Then read `charts/app/values.yaml` and reconcile the workload's resource
request with what a node can actually provide.

## Your task

Make the pod schedulable and the rollout healthy again. Edit **only
`charts/app/values.yaml`**.

## Rules

- Do **not** shrink the request below a footprint the app can start in
  (the anti-cheat enforces a minimum of 32Mi requests / 64Mi limits).
- Do **not** delete the resources block or touch the templates.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
