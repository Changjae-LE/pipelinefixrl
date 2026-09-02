# vh04 - Application binds a port the deployment contract does not expect

## Symptom

The image builds and the release installs, but the pod never becomes Ready:

- `kubectl get pods -n <ns>` shows `0/1`, and the rollout times out.
- Events show `Readiness probe failed` with the connection being refused.
- The chart's port settings agree with each other: the declared container port,
  both probes and the Service all name the same port.
- The container log shows the server reporting the address it bound.

## Investigate

```
kubectl get pods -n <ns>
kubectl get events -n <ns> | grep -i probe
kubectl logs -n <ns> deploy/app | head            # what address did it bind?
kubectl get pod -n <ns> -o jsonpath='{..containerPort}'
```

Then compare the port the application process actually binds with the port the
deployment contract expects it to serve.

## Your task

Make the application serve the port the deployment contract declares. The
chart is **not** the thing to change here.

## Rules

- Do **not** modify anything under `charts/` - the published deployment
  contract is fixed for this task.
- Do **not** delete or weaken the probes.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
