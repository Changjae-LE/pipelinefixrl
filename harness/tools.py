"""Subprocess helpers. Every external tool call goes through here so that:
  * winget-installed tools are found even in an already-open shell, and
  * KUBECONFIG is always pinned to the project kubeconfig.
"""

import glob
import os
import pathlib
import shutil
import subprocess

from harness.paths import KUBECONFIG


def _winget_dirs() -> list[str]:
    home = pathlib.Path(os.environ.get("USERPROFILE") or pathlib.Path.home())
    base = home / "AppData" / "Local" / "Microsoft" / "WinGet"
    cands = [base / "Links"]
    cands += [pathlib.Path(p) for p in glob.glob(str(base / "Packages" / "Kubernetes.kind_*"))]
    cands += [pathlib.Path(p) for p in glob.glob(str(base / "Packages" / "Helm.Helm_*" / "windows-amd64"))]
    cands += [pathlib.Path(p) for p in glob.glob(str(base / "Packages" / "ezwinports.make_*" / "bin"))]
    return [str(p) for p in cands if p.is_dir()]


def _augmented_path() -> str:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    for d in _winget_dirs():
        if d not in parts:
            parts.insert(0, d)
    return os.pathsep.join(parts)


PATH = _augmented_path()


def which(name: str) -> str | None:
    return shutil.which(name, path=PATH)


def run(cmd, *, check=True, capture=True, timeout=None, cwd=None, extra_env=None):
    env = dict(os.environ)
    env["PATH"] = PATH
    env["KUBECONFIG"] = str(KUBECONFIG)
    env.setdefault("PYTHONUTF8", "1")
    if extra_env:
        env.update(extra_env)
    argv = [which(cmd[0]) or cmd[0], *cmd[1:]]
    return subprocess.run(
        argv,
        check=check,
        cwd=str(cwd) if cwd else None,
        env=env,
        timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def kubectl(args, **kw):
    return run(["kubectl", *args], **kw)


def helm(args, **kw):
    return run(["helm", *args], **kw)
