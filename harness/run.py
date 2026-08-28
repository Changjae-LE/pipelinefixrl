"""Orchestrates the core workflow for one variant:
namespace -> build -> kind load -> helm deploy -> collect -> evaluate -> report.

Namespace deletion is a separate step (cli `cleanup-ns`) so evidence can be
inspected live; `make e2e-base` chains the cleanup explicitly.
"""

import datetime
import json
import pathlib

from harness import tools
from harness.collect import collect_all
from harness.evaluate import evaluate, is_healthy
from harness.paths import CHART_DIR, REPO_ROOT, RUNS_DIR, VERSIONS, ensure_state_dirs
from harness.report import write_report

RELEASE = "app"
CLUSTER = VERSIONS["KIND_CLUSTER_NAME"]


def _utc_ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _git_sha() -> str:
    try:
        r = tools.run(["git", "rev-parse", "--short=12", "HEAD"], check=False, cwd=REPO_ROOT)
        return r.stdout.strip() or "nogit"
    except Exception:  # noqa: BLE001
        return "nogit"


def image_tag(variant: str, ts: str | None = None) -> str:
    ts = ts or _utc_ts()
    return f"{VERSIONS['APP_IMAGE_REPO']}:{variant}-{_git_sha()}-{ts}"


def build_and_load(variant: str, tree: pathlib.Path | None = None, tag: str | None = None) -> str:
    tree = tree or REPO_ROOT
    tag = tag or image_tag(variant)
    tools.run(
        ["docker", "build", "-f", str(tree / "docker" / "Dockerfile"), "-t", tag, str(tree)],
        capture=False,
    )
    tools.run(["kind", "load", "docker-image", tag, "--name", CLUSTER], capture=False)
    return tag


def _last_pointer(variant: str) -> pathlib.Path:
    return RUNS_DIR / f"last-{variant}"


def deploy(tag: str, namespace: str, tree: pathlib.Path | None = None, timeout: int = 120) -> dict:
    chart = (tree / "charts" / "app") if tree else CHART_DIR
    repo, version = tag.split(":", 1)
    tools.kubectl(["create", "namespace", namespace])
    tools.kubectl(
        ["label", "namespace", namespace, "pfrl/managed=true", "pfrl/release=" + RELEASE,
         "--overwrite"]
    )
    tools.helm(
        [
            "upgrade", "--install", RELEASE, str(chart),
            "--namespace", namespace,
            "--set", f"image.repository={repo}",
            "--set", f"image.tag={version}",
            "--set", "image.pullPolicy=Never",
        ]
    )
    r = tools.kubectl(
        ["rollout", "status", f"deploy/{RELEASE}", "-n", namespace, f"--timeout={timeout}s"],
        check=False,
    )
    return {
        "rollout_ok": r.returncode == 0,
        "rollout_output": (r.stdout or "") + (r.stderr or ""),
    }


def run_variant(variant: str = "base", expect_healthy: bool = True) -> tuple[str, int, list[dict]]:
    ensure_state_dirs()
    ts = _utc_ts()
    run_id = f"{variant}-{ts}"
    namespace = f"pfrl-{variant}-{ts}".lower()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    timeout = int(VERSIONS.get("DEPLOY_TIMEOUT_SECONDS", "120"))

    tag = image_tag(variant, ts)
    meta = {
        "run_id": run_id,
        "variant": variant,
        "namespace": namespace,
        "image": tag,
        "started_at": ts,
        "cluster": CLUSTER,
        "deploy_timeout_seconds": timeout,
    }

    build_and_load(variant, tag=tag)

    node = f"{CLUSTER}-control-plane"
    imgs = tools.run(["docker", "exec", node, "crictl", "images"], check=False)
    (run_dir / "node-images.txt").write_text(imgs.stdout or imgs.stderr or "")
    meta["image_on_node"] = tag.split(":", 1)[0] in (imgs.stdout or "")

    meta.update(deploy(tag, namespace, timeout=timeout))
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    collect_all(namespace, RELEASE, run_dir)
    checks, score = evaluate(namespace, RELEASE, run_dir, meta)
    text = write_report(run_dir, meta, checks, score)
    print(text)

    _last_pointer(variant).write_text(run_id)

    healthy = is_healthy(checks, score)
    if expect_healthy and not healthy:
        raise SystemExit(f"variant '{variant}' expected healthy but scored {score} (see {run_dir})")
    return run_id, score, checks


def cleanup_namespace(variant: str) -> str:
    run_id = _last_pointer(variant).read_text().strip()
    meta = json.loads((RUNS_DIR / run_id / "meta.json").read_text())
    ns = meta["namespace"]
    tools.kubectl(
        ["delete", "namespace", ns, "--ignore-not-found=true", "--wait=true", "--timeout=120s"],
        check=False,
    )
    return ns
