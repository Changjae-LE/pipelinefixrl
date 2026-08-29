# scenario-003 — OOMKilled crash loop

## Symptom

`helm upgrade --install` of `charts/app` succeeds, but the deployment never
becomes healthy:

- `kubectl get pods -w` shows the pod cycling
  `ContainerCreating` → `Running` → `CrashLoopBackOff`, with `RESTARTS`
  climbing and `READY 0/1`.
- `kubectl describe pod <pod>` shows, for the app container:
  `Last State: Terminated`, `Reason: OOMKilled`, `Exit Code: 137`.
- `kubectl get events` shows `Warning  BackOff  Back-off restarting failed
  container`.
- `kubectl logs <pod>` (current) is empty or truncated — the process is killed
  before it finishes starting. `kubectl logs <pod> --previous` shows only the
  first log lines, then nothing.
- `kubectl get endpoints app` has no ready addresses.

The container is being killed by the kernel out-of-memory killer: the memory it
is allowed to use is smaller than what the application needs just to start.

## Investigate

```
kubectl get pods -w -n <ns>
kubectl describe pod <pod> -n <ns>            # Last State / Reason: OOMKilled / Exit Code: 137
kubectl logs <pod> -n <ns> --previous
kubectl get events -n <ns> --sort-by=.lastTimestamp
helm get values app -n <ns>                   # effective chart values, incl. resources.*
```

Compare the memory the container is granted with what a healthy run of this
application actually consumes at startup.

## Your task

Get the pod to a stable `Ready` state with **0 restarts**. Edit **only
`charts/app/values.yaml`**.

## Rules

- Do **not** remove or empty `resources.requests` or `resources.limits` — the
  deployment must keep resource governance. A "fix" that deletes the limits, or
  that leaves memory below a sane floor, is scored `0` by the anti-cheat check.
- Do **not** change `image.*`, the probes, the container `securityContext`, or
  `replicaCount`.
- Do **not** modify anything under `tests/`.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` (including
`no_oomkill`), `0` restarts, and `SCORE: 100`.
