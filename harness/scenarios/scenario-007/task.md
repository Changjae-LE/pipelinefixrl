# scenario-007 — Misconfigured ConfigMap reference

## Symptom

`helm upgrade --install` of `charts/app` succeeds — the release is `deployed`
and the Deployment is created — but the pod never starts:

```
$ kubectl get pods -n <ns>
NAME                   READY   STATUS                       RESTARTS   AGE
app-7c9d...-x2k4       0/1     CreateContainerConfigError   0          40s
```

`kubectl describe pod` shows the kubelet cannot build the container's
environment:

```
  Warning  Failed  ...  Error: couldn't find key teir in ConfigMap <ns>/app-config
```

The Service therefore has no endpoints and `/` is unreachable.

## Investigate

```
kubectl get pods -n <ns>
kubectl describe pod <pod> -n <ns>
kubectl get configmap app-config -n <ns> -o yaml
kubectl get deploy app -n <ns> -o jsonpath='{.spec.template.spec.containers[0].env}'
helm get values app -n <ns>
```

The container's `APP_TIER` env var is wired through a `configMapKeyRef` whose
`key` comes from a Helm value. Compare the key that reference asks for against
the keys the `app-config` ConfigMap actually defines — they do not match.

## Your task

Make the pod start and serve its configured tier at `GET /` (the JSON response
must include `"tier"` equal to the ConfigMap's value) by **reconciling the
`configMapKeyRef` with the key the ConfigMap actually provides**. Edit **only
`charts/app/values.yaml`**.

## Rules

- Fix the reference — do **not** remove the config dependency: the rendered
  Deployment must still source `APP_TIER` from a `configMapKeyRef`.
- Do **not** inline or hardcode the tier value in `env:` or in the app.
- Do **not** empty, rename, or delete the `app-config` ConfigMap, and do **not**
  edit anything under `charts/app/templates/`, `app/`, or `tests/`.

A solution that trips the anti-cheat check is scored `0`.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` (functional and
`config_applied`) and `SCORE: 100`.
