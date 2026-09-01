"""Ephemeral scenario-tree assembly: base-tree copy, unified-diff patch
application, the frozen tooling-subtree integrity guard, and a byte-diff of a
tree against the repo base (used by compose-check). Moved verbatim from the
former harness/scenario.py; no behavior change."""

import pathlib
import shutil

from harness import tools
from harness.paths import REPO_ROOT

# Only the paths that matter for building + chart deploy + anti-cheat + compose.
_TREE_PATHS = [
    "app",
    "charts",
    "docker",
    "tests",
    "requirements.txt",
    "pyproject.toml",
    ".dockerignore",
    # scenario-009 runs the real scripts/ci.sh from inside the ephemeral tree,
    # so it (and the config/ it sources) must be present. No scenario patch is
    # allowed to touch these — enforced by _assert_frozen_subtrees below.
    "scripts",
    "config",
]

# Subtrees copied into the tree purely as tooling; a scenario break/golden patch
# must never modify them.
_FROZEN_SUBTREES = ("scripts", "config")
_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache"
)

# Transient / git-ignored artifacts that must never count as a tree difference.
_SKIP_PARTS = ("__pycache__", ".pytest_cache", ".ruff_cache")
_SKIP_SUFFIXES = (".pyc", ".pyo")


def _is_transient(path: pathlib.Path) -> bool:
    if any(part in _SKIP_PARTS or part.endswith(".egg-info") for part in path.parts):
        return True
    return path.suffix in _SKIP_SUFFIXES


def _copy_base_tree(dst: pathlib.Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for name in _TREE_PATHS:
        src = REPO_ROOT / name
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst / name, ignore=_IGNORE, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst / name)


def _assert_frozen_subtrees(tree: pathlib.Path) -> None:
    """Every file under scripts/ and config/ in the ephemeral tree must be
    byte-identical to the repo — no scenario patch may touch tooling. Raises
    SystemExit on the first mismatch/missing file (both variants, not just
    golden)."""
    for name in _FROZEN_SUBTREES:
        base = REPO_ROOT / name
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or _is_transient(f):
                continue
            rel = f.relative_to(REPO_ROOT)
            g = tree / rel
            if (not g.exists()) or g.read_bytes() != f.read_bytes():
                raise SystemExit(
                    f"scenario patch modified or dropped a frozen tooling file: {rel.as_posix()}"
                )


def _apply_patch(tree: pathlib.Path, patch: pathlib.Path) -> None:
    """`patch -p1` first (byte-exact on this host), `git apply` as fallback."""
    p = str(patch.resolve())
    r = tools.run(["patch", "-p1", "--forward", "-i", p], cwd=tree, check=False)
    if r.returncode == 0:
        return
    r2 = tools.run(["git", "apply", "--verbose", p], cwd=tree, check=False)
    if r2.returncode != 0:
        raise RuntimeError(
            f"failed to apply {patch.name}\n[patch]\n{r.stdout}{r.stderr}\n"
            f"[git apply]\n{r2.stdout}{r2.stderr}"
        )


def tree_matches_base(tree: pathlib.Path, repo_root: pathlib.Path = REPO_ROOT) -> list[str]:
    """Byte-diff `tree` against the repo base over _TREE_PATHS. Returns the list
    of differing relative paths ([] == identical). This is the comparison behind
    `harness compose-check`."""
    diffs: list[str] = []
    for name in _TREE_PATHS:
        src = repo_root / name
        if not src.exists():
            continue
        if src.is_file():
            if src.read_bytes() != (tree / name).read_bytes():
                diffs.append(name)
            continue
        for f in sorted(src.rglob("*")):
            if f.is_file() and not _is_transient(f):
                rel = f.relative_to(repo_root)
                g = tree / rel
                if (not g.exists()) or g.read_bytes() != f.read_bytes():
                    diffs.append(rel.as_posix())
    return diffs
