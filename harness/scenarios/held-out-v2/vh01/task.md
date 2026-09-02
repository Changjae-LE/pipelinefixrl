# vh01 - Liveness probe cannot reach the container

## Symptom

The rollout reports success, then the workload degrades:

- `kubectl get pods -n <ns>` shows `RESTARTS` climbing.
- `kubectl describe pod` / events show `Liveness probe failed` and `Unhealthy`.
- Readiness is fine; the container is killed and restarted on a cycle.

## Investigate

```
kubectl get pods -n <ns>                                    # restart count
kubectl get events -n <ns> | grep -i probe                  # which probe fails
kubectl get pod -n <ns> -o jsonpath='{..livenessProbe}'     # what it probes
kubectl get pod -n <ns> -o jsonpath='{..containerPort}'     # what the app serves
```

Then read `charts/app/values.yaml` and reconcile the liveness probe with the
port the container actually listens on.

## Your task

Stop the restart cycle. Edit **only `charts/app/values.yaml`**.

## Rules

- Do **not** delete or disable either probe, and do **not** replace the HTTP
  probe with another probe type.
- Do **not** change what port the container listens on.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
