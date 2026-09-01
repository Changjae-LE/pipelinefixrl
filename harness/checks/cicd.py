"""CI-gate check (owning scenario: scenario-009) — runs the real scripts/ci.sh
from inside the ephemeral scenario tree."""

import os
import subprocess
import sys

from harness import tools
from harness.checks import register_scenario_check
from harness.paths import REPO_ROOT


@register_scenario_check("ci_gate_pass", 30)
def _ci_gate_pass(*, run_dir, namespace, release, meta):
    """Owning scenario: scenario-009. Runs the real M-BE ``scripts/ci.sh`` from
    inside the ephemeral scenario tree (pytest -q, helm lint, helm template
    smoke, docker build, :latest / unpinned-base grep) and PASSes iff it exits
    0. Writes the full transcript to ``run_dir/ci.log``. ``scripts/`` and
    ``config/`` are carried into the tree and are guarded byte-identical to
    base, so this scores the submitted tree, not the repo."""
    tree = run_dir / "tree"
    for rel in ("scripts/ci.sh", "scripts/lib.sh"):
        tf = tree / rel
        if (not tf.exists()) or tf.read_bytes() != (REPO_ROOT / rel).read_bytes():
            return False, f"tree {rel} is not byte-identical to base (tooling tampered)"

    env = dict(os.environ)
    env["PATH"] = tools.PATH
    env["PYTHON"] = sys.executable          # the venv interpreter running the harness
    env["PYTHONPATH"] = str(tree)           # make `import app` resolve to the tree
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    exe = tools.which("bash") or "bash"
    try:
        cp = subprocess.run(
            [exe, "scripts/ci.sh"],
            cwd=str(tree),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=420,
        )
        out = (cp.stdout or "") + (cp.stderr or "")
        rc = cp.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") + "\n[timeout] scripts/ci.sh exceeded 420s\n"
        rc = 124
    (run_dir / "ci.log").write_text(out, encoding="utf-8")

    first_fail = next(
        (ln.strip() for ln in out.splitlines() if ln.startswith("FAILED ") or " FAILED" in ln),
        "",
    )
    tail = f" ({first_fail})" if first_fail else ""
    return rc == 0, f"scripts/ci.sh exit {rc}{tail}"
