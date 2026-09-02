# vh02 - Deployment image tag sourced from the wrong values key

## Symptom

The release installs but no container ever starts:

- `kubectl get pods -n <ns>` shows the pod `Pending` with
  `ErrImageNeverPull`.
- `kubectl get pod -n <ns> -o jsonpath='{..image}'` shows a tag that is not the
  tag this build produced.
- The node has the built image under its own unique tag.

## Investigate

```
kubectl get pods -n <ns>
kubectl describe pod -n <ns> | grep -i image
kubectl get pod -n <ns> -o jsonpath='{..image}'     # the tag actually requested
helm get manifest app -n <ns> | grep image:         # how it was rendered
```

Then read `charts/app/templates/deployment.yaml` and reconcile the image
reference with the value the deploy actually supplies the tag through.

## Your task

Make the workload run the image this build produced. Edit **only
`charts/app/templates/deployment.yaml`**.

## Rules

- The image tag is supplied at deploy time; do **not** hard-code a tag, and do
  **not** use `latest`.
- Do **not** remove the guard that makes a missing tag a hard error.
- Do **not** repurpose an unrelated value as the tag.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
