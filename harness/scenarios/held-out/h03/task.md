# h03 — Service published-port contract broken

## Symptom

The workload is completely healthy, but consumers cannot connect:

- `kubectl get deploy app` shows `READY 1/1`; the pod is `Running`, `Ready`,
  `RESTARTS 0`; the EndpointSlice lists a ready address.
- Every consumer of this app connects to the Service on its published port —
  and that connection cannot even be established: the Service does not offer
  the port at all (`kubectl port-forward svc/app <local>:80` fails to resolve
  the port).

## Investigate

```
kubectl get svc app -n <ns> -o jsonpath='{.spec.ports}'   # what the Service publishes
kubectl get endpointslices -n <ns>                        # backend is fine
kubectl get pods -n <ns>                                  # pod is fine
```

Then read `charts/app/values.yaml` and restore the port this Service is
expected to publish to its consumers.

## Your task

Make the Service offer its published contract port again. Edit **only
`charts/app/values.yaml`**.

## Rules

- Do **not** change the container's listen port, the targetPort relationship,
  the selector wiring, or the templates.
- Do **not** add a second Service or extra ports to mask the change.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` (including
`service_ports_wired`) and `SCORE: 100`.
