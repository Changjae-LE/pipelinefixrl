import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE_DIR = REPO_ROOT / ".state"
KUBECONFIG = STATE_DIR / "kubeconfig"
RUNS_DIR = STATE_DIR / "runs"
CLUSTERS_DIR = STATE_DIR / "clusters"
CHART_DIR = REPO_ROOT / "charts" / "app"


def load_versions() -> dict:
    env: dict[str, str] = {}
    p = REPO_ROOT / "config" / "versions.env"
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


VERSIONS = load_versions()


def ensure_state_dirs() -> None:
    for d in (STATE_DIR, RUNS_DIR, CLUSTERS_DIR):
        d.mkdir(parents=True, exist_ok=True)
