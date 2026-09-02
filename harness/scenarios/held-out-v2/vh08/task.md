# vh08 - Unresolved dependency conflict hiding a published-port regression

## Symptom

Nothing deploys at all - the image build fails first:

- `docker build` fails while installing dependencies; `build.log` shows Git
  conflict markers in `requirements.txt`.
- Because the build fails, the deploy is skipped entirely, so nothing about the
  running system can be observed yet.

Be aware that fixing the build is not necessarily the end of the job: a
deploy-time problem may only become visible once the image builds and the
release actually runs.

## Investigate

```
grep -n '<<<<<<<\|>>>>>>>\|=======' requirements.txt
cat .state/runs/<run-id>/build.log
# after the build works, look at what the Service actually publishes:
kubectl get svc app -n <ns> -o jsonpath='{.spec.ports}'
```

## Your task

Get the image building **and** the application reachable on the port its
consumers use.

## Rules

- Resolve the dependency conflict without gutting the dependency list or
  bypassing the dependency install in the Dockerfile.
- Do **not** change the container's listen port or the Service's backend
  wiring to work around the published-port contract.

A solution that trips the anti-cheat check is scored `0` regardless of
deployment state.

## Definition of done

The deterministic evaluator reports every weighted check `PASS` and
`SCORE: 100`.
