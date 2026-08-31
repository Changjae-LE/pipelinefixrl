# scenario-005 — Service selects no pods

## Symptom

`helm upgrade --install` of `charts/app` succeeds and the workload is healthy —
but nothing can reach it through the Service:

- `kubectl get deploy app` shows `READY 1/1`; `kubectl get pods` shows the pod
  `Running`, `Ready`, `RESTARTS 0`. The rollout completed normally.
- `kubectl get endpoints app -n <ns>` is **empty**; `kubectl get endpointslices
  -n <ns>` has no addresses for the Service.
- `GET /health` through the Service (`svc/app`) hangs or is refused — there is
  no backend to route to.
- `kubectl describe svc app` shows a selector, and `kubectl get pods
  --show-labels` shows the pod's labels — they do not agree.

The pod is fine. The Service is looking for pods that carry labels the
application's pods do not have, so it selects nothing.

## Investigate

```
kubectl get deploy app -n <ns>                                   # 1/1 Ready
kubectl get pods --show-labels -n <ns>                           # the pod's actual labels
kubectl get endpoints app -n <ns>                                # empty
kubectl get endpointslices -n <ns>
kubectl get svc app -n <ns> -o jsonpath='{.spec.selector}'       # what the Service looks for
kubectl get deploy app -n <ns> -o jsonpath='{.spec.selector.matchLabels}'
```

Then read `charts/app/templates/service.yaml` and reconcile the Service's
`selector` with the labels the Deployment actually puts on its pods.

## Your task

Make the Service route to the application's pods again. Edit **only
`charts/app/templates/service.yaml`**.

## Rules

- Do **not** change the Deployment's selector or the pod labels, and do **not**
  touch `charts/app/templates/deployment.yaml`, `charts/app/values.yaml`, or
  anything under `tests/`.
- Do **not** add a second Service or a `type: ExternalName` shim.
- Do **not** leave the Service selector empty or pointing at labels no pod
  carries.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` (including
`service_selects_pods`) and `SCORE: 100`.
