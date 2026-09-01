"""Fixtures for the fast harness tests. No Docker / kind / K8s / network / `patch`."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # tests/ on path for _diffapply

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def run_dir(tmp_path):
    """Factory: run_dir(**{artifact_name: python_obj_or_str}) -> Path.

    Dicts/lists are json-dumped; strings are written verbatim; a 'logs' value of
    {name: text} writes tmp/logs/name.
    """
    def _make(**artifacts):
        d = tmp_path / "run"
        d.mkdir(exist_ok=True)
        for name, val in artifacts.items():
            if name == "logs":
                ld = d / "logs"
                ld.mkdir(exist_ok=True)
                for fn, txt in val.items():
                    (ld / fn).write_text(txt, encoding="utf-8")
            elif isinstance(val, (dict, list)):
                (d / name).write_text(json.dumps(val), encoding="utf-8")
            else:
                (d / name).write_text(str(val), encoding="utf-8")
        return d
    return _make


@pytest.fixture
def base_tree(tmp_path):
    """A fresh base scenario tree (harness.patching._copy_base_tree)."""
    from harness.patching import _copy_base_tree
    t = tmp_path / "tree"
    _copy_base_tree(t)
    return t


def apply_patch(tree: pathlib.Path, patch_path: pathlib.Path):
    """Pure-Python patch apply (no system `patch`)."""
    import _diffapply
    return _diffapply.apply(tree, patch_path)


# --- small builders for fabricated k8s artifacts ---------------------------

def healthy_pod(labels=None):
    return {
        "spec": {"containers": [{"name": "app", "securityContext": {
            "runAsNonRoot": True, "runAsUser": 1000, "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}}]},
        "metadata": {"name": "app-abc", "labels": labels or {}},
        "status": {"conditions": [{"type": "Ready", "status": "True"}],
                   "containerStatuses": [{"name": "app", "restartCount": 0, "ready": True}]},
    }


def pods(*items):
    return {"items": list(items)}
