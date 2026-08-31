# scenario-004 — Helm value not wired to the template

## Symptom

`helm upgrade --install` of `charts/app` **succeeds**, but the deployment never
rolls out:

- `kubectl get pods` shows the pod `Pending`, `0/1`, `RESTARTS 0`, with
  `STATUS = InvalidImageName`.
- `kubectl describe pod <pod>` shows
  `Failed to apply default image tag "pipelinefixrl/app:": couldn't parse image
  reference "pipelinefixrl/app:": invalid reference format` and
  `Error: InvalidImageName`.
- `kubectl get endpoints app` has no ready addresses.
- The container never starts — the image reference it is given is not a valid
  one.

The pipeline built the image and passed its unique tag to Helm with
`--set image.tag=<tag>`, yet the rendered Deployment carries an **empty** tag:
`image: "pipelinefixrl/app:"`. The tag you supplied is being ignored.

## Investigate

```
kubectl get pods -n <ns>
kubectl describe pod <pod> -n <ns>                 # InvalidImageName / invalid reference format
kubectl get events -n <ns> --sort-by=.lastTimestamp
helm get manifest app -n <ns> | grep 'image:'      # rendered image reference — note the empty tag
helm get values  app -n <ns>                       # the tag you set IS present in the values
```

Then read `charts/app/templates/deployment.yaml` and work out why the value that
is present in `helm get values` never reaches the rendered `image:` field.

## Your task

Make the Deployment run the image whose tag the pipeline supplied via
`--set image.tag`. Edit **only `charts/app/templates/deployment.yaml`**.

## Rules

- Do **not** hard-code an image tag in the template.
- Do **not** remove the required-value guard on the image tag.
- Do **not** change how `image.repository` or `image.pullPolicy` are wired, and
  do **not** touch the probe, `env`, `resources`, or `securityContext` blocks.
- Do **not** modify `charts/app/values.yaml`, anything under `tests/`, or any
  other file.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
