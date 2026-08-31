# scenario-006 — Container runs as root

## Symptom

`helm upgrade --install` of `charts/app` succeeds and the deployment is fully
healthy — rollout completes, the pod is `Ready`, `GET /health` returns `200` —
but the container is running with a non-compliant runtime security posture:

- `kubectl exec <pod> -n <ns> -- id` reports `uid=0(root)`.
- `kubectl exec <pod> -n <ns> -- sh -c 'touch /x && echo writable'` succeeds —
  the root filesystem is writable.
- `kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[0].securityContext}'`
  shows privilege escalation allowed and no dropped capabilities.

Nothing about "is it Ready?" tells you this — the workload is green. The
grade is failing because the pod is not hardened.

## Investigate

```
kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[0].securityContext}'
kubectl exec <pod> -n <ns> -- id
kubectl exec <pod> -n <ns> -- sh -c 'touch /probe 2>&1 || echo readonly'
helm get values app -n <ns>
kubectl get pod <pod> -n <ns> -o yaml | sed -n '/securityContext/,+8p'
```

## Your task

Restore a hardened runtime posture — the container must run **as a non-root
user**, with a **read-only root filesystem**, **no privilege escalation**, and
**all Linux capabilities dropped** — **without breaking the application**
(`/health` must still return `200`). Edit **only `charts/app/values.yaml`**.

## Rules

- The application must stay up: every functional check must still pass.
- Do **not** remove `podSecurityContext.seccompProfile: RuntimeDefault`.
- Do **not** add a privileged or `SYS_ADMIN` container / sidecar, and do **not**
  mount a volume over `/` to fake a read-only rootfs.
- Do **not** touch `charts/app/templates/`, anything under `tests/`, or any
  other file.

A solution that trips the anti-cheat check is scored `0`.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` (functional and
posture) and `SCORE: 100`.
