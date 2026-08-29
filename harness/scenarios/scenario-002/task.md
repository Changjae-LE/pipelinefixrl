# scenario-002 — Wrong pinned image tag (Never policy)

## Symptom

`helm upgrade --install` of `charts/app` succeeds, but the deployment never
rolls out:

- `kubectl get pods` shows the pod `Pending`, `0/1`, `RESTARTS 0`, with
  `STATUS = ErrImageNeverPull`.
- `kubectl describe pod` shows, for example:
  `Container image "pipelinefixrl/app:v0.0.0-not-loaded" is not present with
  pull policy of Never`.
- `kubectl get endpoints app` has no ready addresses; nothing reaches the
  Service.
- The container never starts — it is not crash-looping. The image it is told to
  run simply cannot be obtained.

The pipeline built the application image and loaded it onto the kind node under a
unique tag, and passed that tag to Helm via `--set image.tag=...`. The image
reference the pod is actually trying to run is a different one that was never
built and never loaded.

## Investigate

```
kubectl get pods -n <ns>
kubectl describe pod <pod> -n <ns>
kubectl get events -n <ns> --sort-by=.lastTimestamp
helm get manifest app -n <ns> | grep 'image:'            # the deployed image reference
helm get values  app -n <ns>                             # effective chart values
docker exec pipelinefixrl-control-plane crictl images    # images present on the node
```

Compare the image reference in the deployed manifest with the image tag the
pipeline actually loaded onto the node.

## Your task

Make the deployment run the image the pipeline built and loaded into kind. Edit
**only `charts/app/values.yaml`**.

## Rules

- Do **not** change `image.pullPolicy` — it must stay `Never` (or
  `IfNotPresent`); "fixing" it by allowing pulls is scored `0`.
- Do **not** access a registry: no `imagePullSecrets`, no registry endpoint, no
  init container or sidecar that fetches an image.
- Do **not** rebuild or `kind load` a different image, and do **not** point the
  deployment at any image other than the one already present on the node.
- Do **not** change `image.repository`.
- Do **not** modify anything under `tests/`, and do not remove or weaken the
  probes or the container `securityContext`.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` (including
`image_pull_ok`) and `SCORE: 100`.
