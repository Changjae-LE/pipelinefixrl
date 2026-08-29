# scenario-001 — Incorrect readiness probe path

## Symptom

After `helm upgrade --install` of `charts/app`, the release installs but the
deployment never finishes rolling out:

- `kubectl rollout status deploy/app` times out.
- `kubectl get deploy app` shows `READY 0/1`; `readyReplicas` stays `0`.
- `kubectl get endpoints app` (or the EndpointSlice) has **no ready addresses**,
  so nothing can reach the Service.
- Pods are **not** crash-looping — `RESTARTS` stays `0`. They are `Running` but
  never `Ready`.
- `kubectl describe pod` / namespace events show repeated
  `Warning  Unhealthy  Readiness probe failed: HTTP probe failed with statuscode: 404`.
- The container log shows the probe requests returning `404`.

## Your task

Make the base application deploy healthy again: the rollout must complete, the
single pod must become Ready with `0` restarts, the Service must have a ready
endpoint, and `GET /health` through the Service must return `200`
`{"status":"ok"}`.

## Rules

- Edit only `charts/app/values.yaml`.
- Do **not** remove or disable the readiness or liveness probe.
- Do **not** change the container `securityContext` (`runAsNonRoot`,
  `allowPrivilegeEscalation`, `readOnlyRootFilesystem`).
- Do **not** modify anything under `tests/`.
- Do **not** set `replicaCount` to `0`.

A solution that breaks any of those rules is scored `0` by the anti-cheat check,
regardless of deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
