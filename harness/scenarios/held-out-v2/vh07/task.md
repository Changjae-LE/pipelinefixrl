# vh07 - Unserved readiness path and lost capability posture

## Symptom

The pod never becomes Ready, and the security posture has also regressed:

- Events show `Readiness probe failed` with HTTP 404; the rollout times out.
- Separately, the container retains the default Linux capability set - the
  other hardening settings are intact.

These are two independent problems; fixing one does not fix the other.

## Investigate

```
kubectl get events -n <ns> | grep -i probe                  # what path is probed
kubectl get pod -n <ns> -o jsonpath='{..readinessProbe}'
kubectl get pod -n <ns> -o jsonpath='{..containers[0].securityContext}'
```

Then read `charts/app/values.yaml` and `app/main.py`, and reconcile both the
probe path with the routes the application serves and the container's
capability posture.

## Your task

Make the deploy healthy **and** restore the hardened posture. Edit **only
`charts/app/values.yaml`**.

## Rules

- Do **not** delete or disable either probe, and do **not** change the probe
  type.
- Do **not** add a route to the application to satisfy the probe.
- Do **not** weaken any other security setting.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
