# vh05 - Pod-level seccomp baseline dropped

## Symptom

The workload is completely healthy - `Running`, `Ready`, 0 restarts, `GET
/health` returns 200 - but the pod no longer meets the platform's security
baseline:

- `kubectl get pod -n <ns> -o jsonpath='{.items[0].spec.securityContext}'`
  is empty.
- The container-level security settings are all still correct.

## Investigate

```
kubectl get pod -n <ns> -o jsonpath='{.items[0].spec.securityContext}'
kubectl get pod -n <ns> -o jsonpath='{..containers[0].securityContext}'
```

Then read `charts/app/values.yaml` and restore the pod-level security baseline
this platform requires.

## Your task

Bring the pod back to the platform security baseline. Edit **only
`charts/app/values.yaml`**.

## Rules

- Do **not** weaken the container-level security settings.
- Do **not** grant the workload additional privileges.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
