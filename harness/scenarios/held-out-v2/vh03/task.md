# vh03 - Container keeps all Linux capabilities

## Symptom

Nothing is visibly broken: the deploy is healthy, the pod is `Running` and
`Ready`, and `GET /health` returns 200. The security posture is not:

- `kubectl get pod -n <ns> -o jsonpath='{..securityContext}'` shows the
  container retains the default Linux capability set.
- The other hardening settings (non-root user, read-only root filesystem, no
  privilege escalation) are intact.

## Investigate

```
kubectl get pod -n <ns> -o jsonpath='{..containers[0].securityContext}'
```

Then read `charts/app/values.yaml` and restore the container's capability
posture.

## Your task

Bring the container back to the project's hardened posture. Edit **only
`charts/app/values.yaml`**.

## Rules

- Do **not** weaken any other security setting to compensate.
- Do **not** add privileged capabilities.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
