# scenario-010 — Unresolved merge conflict

## Symptom

Nothing deploys — the container image never builds:

```
$ make scenario-010-broken      # (or the harness build step)
 => ERROR [app 4/4] RUN pip install --no-cache-dir -r requirements.txt
 ...
 ERROR: Invalid requirement: a conflict marker line was found in requirements.txt
The command '/bin/sh -c pip install ... -r requirements.txt' returned a non-zero code: 1
```

`requirements.txt` was committed with an **unfinished merge** still in it: the
lines beginning with `<`x7, `=`x7, and `>`x7 (the Git conflict markers) plus
both sides of the change. `pip` treats every one of those as a requirement and
aborts.

## Investigate

```
git status
git diff --check                     # points at the conflict marker lines
cat requirements.txt                 # both branches' versions are still here
grep -nE '^(<{7}|={7}|>{7})' requirements.txt
```

## Your task

Finish the merge: edit **`requirements.txt`** so it is a valid pip requirements
file — no conflict marker lines, one coherent set of pinned dependencies that
still installs `fastapi` and `uvicorn`. Then the image builds, the app deploys,
and every check passes.

## Rules

- Resolve the conflict in `requirements.txt` only. Do **not** edit
  `docker/Dockerfile`, `charts/`, `app/`, `scripts/`, `config/`, or `tests/`.
- The Dockerfile must still `pip install` from `requirements.txt` — do not
  bypass dependency installation.
- Do not delete the dependencies; `fastapi` and `uvicorn` must remain declared
  with version constraints.

A solution that trips the anti-cheat check is scored `0`.

## Definition of done

`docker build` exits `0`, no tracked file contains a conflict marker line, the
deployment is healthy, every weighted check reports `PASS`, and `SCORE: 100`.
