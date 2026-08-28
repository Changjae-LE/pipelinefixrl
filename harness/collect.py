"""Step 6 of the core workflow: capture cluster state as files under the run dir."""

import json
import pathlib

from harness import tools


def _save(run_dir: pathlib.Path, name: str, args: list[str]) -> None:
    r = tools.kubectl(args, check=False)
    body = r.stdout or ""
    if r.returncode != 0 and r.stderr:
        body += f"\n[stderr]\n{r.stderr}"
    (run_dir / name).write_text(body)


def collect_all(namespace: str, release: str, run_dir: pathlib.Path) -> None:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    _save(run_dir, "events.txt", ["get", "events", "-n", namespace, "--sort-by=.lastTimestamp"])
    _save(run_dir, "events.json", ["get", "events", "-n", namespace, "-o", "json"])
    _save(run_dir, "rollout.json", ["get", "deploy", release, "-n", namespace, "-o", "json"])
    _save(run_dir, "replicasets.json", ["get", "rs", "-n", namespace, "-o", "json"])
    _save(run_dir, "pods.json", ["get", "pods", "-n", namespace, "-o", "json"])
    # readiness.json is kept as a distinct artifact name per PROJECT_SPEC step 6.
    _save(run_dir, "readiness.json", ["get", "pods", "-n", namespace, "-o", "json"])
    _save(run_dir, "services.json", ["get", "svc", "-n", namespace, "-o", "json"])
    _save(run_dir, "endpoints.json", ["get", "endpoints", "-n", namespace, "-o", "json"])
    _save(run_dir, "endpointslices.json", ["get", "endpointslices", "-n", namespace, "-o", "json"])

    hs = tools.helm(["status", release, "-n", namespace, "-o", "json"], check=False)
    (run_dir / "helm-status.json").write_text(hs.stdout or hs.stderr or "{}")

    try:
        pods = json.loads((run_dir / "pods.json").read_text())
    except (json.JSONDecodeError, ValueError):
        pods = {"items": []}

    for pod in pods.get("items", []):
        pn = pod["metadata"]["name"]
        for c in pod.get("spec", {}).get("containers", []):
            cn = c["name"]
            cur = tools.kubectl(["logs", "-n", namespace, pn, "-c", cn, "--tail=-1"], check=False)
            (logs_dir / f"{pn}.{cn}.log").write_text(cur.stdout or cur.stderr or "")
            prev = tools.kubectl(
                ["logs", "-n", namespace, pn, "-c", cn, "--previous", "--tail=-1"], check=False
            )
            if prev.returncode == 0 and prev.stdout:
                (logs_dir / f"{pn}.{cn}.previous.log").write_text(prev.stdout)
