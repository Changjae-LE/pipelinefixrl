# h01 — Service targetPort does not reach the container

## Symptom

The deploy looks healthy but nothing gets through the Service:

- `kubectl get deploy app` shows `READY 1/1`; the pod is `Running`, `Ready`,
  `RESTARTS 0`.
- `kubectl get endpointslices -n <ns>` lists a **ready** endpoint address.
- `GET /health` through `svc/app` is refused — the connection reaches a pod
  but nothing is listening on the port the Service forwards to.

## Investigate

```
kubectl get pods -n <ns>                                          # Running / Ready
kubectl get endpointslices -n <ns>                                # ready address present
kubectl get svc app -n <ns> -o jsonpath='{.spec.ports}'           # what the Service forwards to
kubectl get pods -n <ns> -o jsonpath='{..containerPort}'          # what the container listens on
```

Then read `charts/app/values.yaml` and reconcile the Service's forwarding
target with the port the container actually serves.

## Your task

Make traffic through the Service reach the application again. Edit **only
`charts/app/values.yaml`**.

## Rules

- Do **not** change the Service's published port, the selector wiring, the
  probes' target port relationship, or the templates.
- Do **not** change what port the container listens on to paper over the
  Service.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` (including
`service_ports_wired`) and `SCORE: 100`.
