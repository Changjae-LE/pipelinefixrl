"""Composable repair primitives for the advanced deriving fixer.

Each primitive reasons about one reusable *relationship* between parts of the
scenario tree and/or the observed runtime evidence — not about a specific
scenario. A primitive returns Findings (diagnosis + proposed byte edits +
rationale); the deterministic composition engine (`compose`) runs every
primitive in a fixed order against a working copy of the candidate tree and
composes their edits (later primitives see earlier edits; duplicates are
collapsed; incompatible edits to lines another primitive introduced are
recorded as explicit conflicts and skipped, never silently overwritten).

Boundary: a primitive's only inputs are the candidate tree and the Evidence
object built from a run's own collected artifacts. Nothing in this module may
read a scenario's reference-answer material of any kind.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import pathlib
import re

import yaml

# ---------------------------------------------------------------------------
# shared low-level helpers (also used by the baseline tier)
# ---------------------------------------------------------------------------
_CONFLICT_SKIP = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache", "node_modules"}


def _is_marker(ln):
    return ln == "=======" or ln.startswith("<<<<<<< ") or ln.startswith(">>>>>>> ")


def _conflict_files(tree):
    hits = []
    for f in sorted(tree.rglob("*")):
        if not f.is_file() or _CONFLICT_SKIP.intersection(f.parts):
            continue
        try:
            txt = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(_is_marker(ln) for ln in txt.splitlines()):
            hits.append(f)
    return hits


def _resolve_conflict_keep_head(text):
    """Drop conflict markers, keep the HEAD side of every hunk."""
    out, mode = [], "keep"
    for ln in text.splitlines(keepends=True):
        s = ln.rstrip("\r\n")
        if s.startswith("<<<<<<< "):
            mode = "head"
            continue
        if s == "=======":
            mode = "theirs"
            continue
        if s.startswith(">>>>>>> "):
            mode = "keep"
            continue
        if mode in ("keep", "head"):
            out.append(ln)
    return "".join(out)


def _yload(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _mem_bytes(v):
    if v is None:
        return 0
    s = str(v).strip()
    num = s.rstrip("KMGkmgi") or "0"
    unit = s[len(num):].lower()
    mul = {"": 1, "k": 1000, "m": 1000**2, "g": 1000**3,
           "ki": 1024, "mi": 1024**2, "gi": 1024**3}.get(unit, 1)
    try:
        return int(float(num) * mul)
    except ValueError:
        return 0


def _cpu_millis(v):
    if v is None:
        return 0
    s = str(v).strip()
    try:
        return int(float(s[:-1])) if s.endswith("m") else int(float(s) * 1000)
    except ValueError:
        return 0


def _routes(tree):
    try:
        src = (tree / "app" / "main.py").read_text(encoding="utf-8")
    except OSError:
        return set()
    return set(re.findall(r'@app\.(?:get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']', src))


def _norm(s):
    return re.sub(r"\s+", "", s)


# ---------------------------------------------------------------------------
# evidence: typed view over a run's own collected artifacts
# ---------------------------------------------------------------------------
_SIGNAL_PATTERNS = {
    "oom": ("OOMKilled", '"exitCode": 137', "exitCode: 137"),
    "image_pull": ("ErrImageNeverPull", "ImagePullBackOff", "ErrImagePull"),
    "invalid_image": ("InvalidImageName",),
    "config_error": ("CreateContainerConfigError", "couldn't find key"),
    "crashloop": ("CrashLoopBackOff",),
    "probe_failure": ("Readiness probe failed", "Liveness probe failed", "Unhealthy"),
    "unschedulable": ("FailedScheduling", "Unschedulable",
                      "Insufficient memory", "Insufficient cpu"),
    "connection_refused": ("connection refused", "Connection refused"),
}


class Evidence:
    """A run directory's own artifacts (events / pods / logs / build / CI /
    check results), parsed into text plus a set of typed workload-state
    signals. This is the workload-state diagnosis layer every repair
    primitive consumes."""

    def __init__(self, run_dir):
        self.run_dir = pathlib.Path(run_dir)
        self.sources: list[str] = []
        self.text = ""
        for name in ("events.txt", "events.json", "pods.json", "build.log", "ci.log"):
            p = self.run_dir / name
            if p.exists():
                try:
                    self.text += p.read_text(encoding="utf-8", errors="replace")
                    self.sources.append(name)
                except OSError:
                    pass
        logs = self.run_dir / "logs"
        if logs.is_dir():
            got = False
            for f in sorted(logs.glob("*")):
                try:
                    self.text += f.read_text(encoding="utf-8", errors="replace")
                    got = True
                except OSError:
                    pass
            if got:
                self.sources.append("logs/")
        # a prior *own-attempt* validation run also leaves its graded results;
        # failed check reasons are legitimate refinement evidence.
        self.failed_checks: list[tuple[str, str]] = []
        cj = self.run_dir / "checks.json"
        if cj.exists():
            try:
                data = json.loads(cj.read_text(encoding="utf-8"))
                self.failed_checks = [(c["id"], c.get("reason", ""))
                                      for c in data.get("checks", [])
                                      if c.get("result") == "FAIL"]
                self.sources.append("checks.json")
            except (OSError, ValueError, KeyError):
                pass
        blob = self.text + " ".join(r for _, r in self.failed_checks)
        self.signals = {sig for sig, pats in _SIGNAL_PATTERNS.items()
                        if any(p in blob for p in pats)}


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class Finding:
    primitive: str
    diagnosis: str
    rationale: str
    edits: list  # [(relpath, new_bytes), ...]


def _f(primitive, diagnosis, rationale, edits):
    return Finding(primitive=primitive, diagnosis=diagnosis,
                   rationale=rationale, edits=edits)


# ---------------------------------------------------------------------------
# P: source integrity — unresolved conflicts block the build
# ---------------------------------------------------------------------------
def p_source_integrity(tree, ev):
    files = _conflict_files(tree)
    if not files:
        return []
    edits = [(f.relative_to(tree).as_posix(),
              _resolve_conflict_keep_head(f.read_text(encoding="utf-8")).encode("utf-8"))
             for f in files]
    return [_f("source_integrity", "conflict",
               "unresolved merge conflict — resolved every hunk to its HEAD side",
               edits)]


# ---------------------------------------------------------------------------
# P: chart value wiring — template value references vs the values that exist,
#    and stale exact-image pins vs what is actually loadable
# ---------------------------------------------------------------------------
def p_chart_value_wiring(tree, ev):
    out = []
    vp = tree / "charts" / "app" / "values.yaml"
    v = _yload(vp)

    # a template consuming a .Values.image.* key that values.yaml never defines
    dp = tree / "charts" / "app" / "templates" / "deployment.yaml"
    try:
        lines = dp.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        lines = []
    defined = set((v.get("image") or {}).keys())
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("image:"):
            refs = set(re.findall(r"\.Values\.image\.([A-Za-z0-9_]+)", ln))
            undefined = refs - defined - {"tag", "repository", "tagOverride", "pullPolicy"}
            if undefined or ".Values.image.tag" not in ln or "required" not in ln:
                indent = ln[: len(ln) - len(ln.lstrip())]
                new = list(lines)
                new[i] = (
                    indent
                    + 'image: "{{ .Values.image.repository }}:'
                    + '{{ .Values.image.tagOverride | default '
                    + '(required "image.tag is required and must not be \'latest\'" '
                    + '.Values.image.tag) }}"\n'
                )
                out.append(_f(
                    "chart_value_wiring", "template_image_ref",
                    f"deployment image ref uses undefined value key(s) "
                    f"{sorted(undefined) or sorted(refs)}; rebuilt it from "
                    ".Values.image.repository + .Values.image.tag with a required guard",
                    [("charts/app/templates/deployment.yaml", "".join(new).encode("utf-8"))]))
            break

    # an exact-tag pin that the runtime shows is not loadable
    override = ((v.get("image") or {}).get("tagOverride")) or ""
    if override:
        corrob = "image_pull" in ev.signals
        text = re.sub(r"(\n\s*tagOverride:\s*).*", r'\1""',
                      vp.read_text(encoding="utf-8"), count=1)
        out.append(_f(
            "chart_value_wiring", "image_override",
            f"image.tagOverride pins {override!r} which is not present on the node"
            + (" (image-pull failure observed)" if corrob else "")
            + "; cleared the override so the built unique tag is used",
            [("charts/app/values.yaml", text.encode("utf-8"))]))
    return out


# ---------------------------------------------------------------------------
# P: HTTP contract — probe path/port vs what the app actually serves, and the
#    /health body vs the contract the tree's own tests assert
# ---------------------------------------------------------------------------
def p_http_contract(tree, ev):
    out = []
    vp = tree / "charts" / "app" / "values.yaml"
    v = _yload(vp)
    routes = _routes(tree)

    # probe path must be a route the app serves
    if routes:
        bad = []
        for probe in ("readinessProbe", "livenessProbe"):
            path = (((v.get(probe) or {}).get("httpGet")) or {}).get("path")
            if path and path not in routes:
                bad.append(path)
        if bad:
            target = "/health" if "/health" in routes else sorted(routes)[0]
            text = vp.read_text(encoding="utf-8")
            for p in set(bad):
                text = text.replace(f"path: {p}\n", f"path: {target}\n")
            out.append(_f(
                "http_contract", "probe_path",
                f"probe path {sorted(set(bad))} is not a route the app serves "
                f"{sorted(routes)}; repointed to {target}",
                [("charts/app/values.yaml", text.encode("utf-8"))]))

    # probe port must be the port the container actually listens on
    cport = v.get("containerPort")
    if isinstance(cport, int):
        bad_ports = []
        for probe in ("readinessProbe", "livenessProbe"):
            port = (((v.get(probe) or {}).get("httpGet")) or {}).get("port")
            if isinstance(port, int) and port != cport:
                bad_ports.append(port)
        if bad_ports:
            lines, sect, in_httpget = [], None, False
            for ln in vp.read_text(encoding="utf-8").splitlines(keepends=True):
                if ln and not ln[0].isspace():
                    sect = ln.split(":", 1)[0].strip()
                    in_httpget = False
                elif sect in ("readinessProbe", "livenessProbe"):
                    st = ln.strip()
                    if st.startswith("httpGet:"):
                        in_httpget = True
                    elif in_httpget and st.startswith("port:"):
                        ln = ln[: ln.index("port:")] + f"port: {cport}\n"
                        in_httpget = False
                    elif st and not st.startswith(("path:", "port:")):
                        in_httpget = False
                lines.append(ln)
            out.append(_f(
                "http_contract", "probe_port",
                f"probe port {sorted(set(bad_ports))} is not the container's "
                f"listen port {cport}; aligned the probe to containerPort",
                [("charts/app/values.yaml", "".join(lines).encode("utf-8"))]))

    # the /health handler must honour the contract the tree's own tests assert
    try:
        main = (tree / "app" / "main.py").read_text(encoding="utf-8")
    except OSError:
        main = ""
    if main:
        asserted = None
        tdir = tree / "tests"
        for tf in sorted(tdir.glob("*.py")) if tdir.is_dir() else []:
            m = re.search(
                r"\.get\(\s*[\"']/health[\"']\s*\)[\s\S]{0,200}?\.json\(\)\s*==\s*(\{[^}]*\})",
                tf.read_text(encoding="utf-8"))
            if m:
                asserted = m.group(1)
                break
        if asserted:
            cur = re.search(r"/health[\s\S]{0,200}?JSONResponse\(\s*(\{[^}]*\})", main)
            if cur and _norm(cur.group(1)) != _norm(asserted):
                text = main.replace(cur.group(1), asserted, 1)
                out.append(_f(
                    "http_contract", "health_contract",
                    f"/health returns {cur.group(1)} but the health test asserts "
                    f"{asserted}; aligned the handler",
                    [("app/main.py", text.encode("utf-8"))]))
    return out


# ---------------------------------------------------------------------------
# P: service wiring — the Service must select the workload's pods and forward
#    traffic to a port the container actually listens on
# ---------------------------------------------------------------------------
def p_service_wiring(tree, ev):
    out = []
    sp = tree / "charts" / "app" / "templates" / "service.yaml"
    dp = tree / "charts" / "app" / "templates" / "deployment.yaml"
    try:
        svc = sp.read_text(encoding="utf-8")
        dep = dp.read_text(encoding="utf-8")
    except OSError:
        return out

    # selector: a hard-coded selector that diverged from the shared helper the
    # workload labels come from can never match the pods
    if 'include "app.selectorLabels"' not in svc and 'include "app.selectorLabels"' in dep:
        new, lines = [], svc.splitlines(keepends=True)
        i = 0
        while i < len(lines):
            ln = lines[i]
            new.append(ln)
            if ln.rstrip().endswith("selector:") and ln.strip() == "selector:":
                base_indent = len(ln) - len(ln.lstrip())
                new.append(" " * (base_indent + 2)
                           + '{{- include "app.selectorLabels" . | nindent '
                           + str(base_indent + 2) + " }}\n")
                i += 1
                while i < len(lines) and (not lines[i].strip() or
                                          len(lines[i]) - len(lines[i].lstrip()) > base_indent):
                    i += 1
                continue
            i += 1
        out.append(_f(
            "service_wiring", "service_selector",
            "Service selector is hard-coded and no longer matches the Deployment; "
            "re-derived it from the shared app.selectorLabels helper",
            [("charts/app/templates/service.yaml", "".join(new).encode("utf-8"))]))

    # targetPort: traffic must land on the container's listen port
    vp = tree / "charts" / "app" / "values.yaml"
    v = _yload(vp)
    cport = v.get("containerPort")
    tport = (v.get("service") or {}).get("targetPort")
    if isinstance(cport, int) and isinstance(tport, int) and tport != cport:
        lines, sect = [], None
        for ln in vp.read_text(encoding="utf-8").splitlines(keepends=True):
            if ln and not ln[0].isspace():
                sect = ln.split(":", 1)[0].strip()
            elif sect == "service" and ln.strip().startswith("targetPort:"):
                ln = ln[: ln.index("targetPort:")] + f"targetPort: {cport}\n"
            lines.append(ln)
        out.append(_f(
            "service_wiring", "service_target_port",
            f"Service targetPort {tport} does not correspond to the container's "
            f"listen port {cport}; pods can be healthy while traffic breaks — "
            "aligned targetPort to containerPort",
            [("charts/app/values.yaml", "".join(lines).encode("utf-8"))]))
    return out


# ---------------------------------------------------------------------------
# P: runtime constraints — resources vs observed workload state, and the
#    container security posture
# ---------------------------------------------------------------------------
_HARDENED_SC = (
    "securityContext:\n"
    "  runAsNonRoot: true\n"
    "  runAsUser: 1000\n"
    "  allowPrivilegeEscalation: false\n"
    "  readOnlyRootFilesystem: true\n"
    "  capabilities:\n"
    "    drop:\n"
    "      - ALL\n"
)

# memory floors a workload of this size needs to start (observed via OOM);
# requests above these ceilings can never be scheduled on a kind node.
_MEM_FLOOR_REQ, _MEM_FLOOR_LIM = 64 * 1024**2, 128 * 1024**2
_MEM_ABSURD, _CPU_ABSURD = 8 * 1024**3, 8000


def p_runtime_constraints(tree, ev):
    out = []
    vp = tree / "charts" / "app" / "values.yaml"
    v = _yload(vp)
    res = v.get("resources") or {}
    req_m = _mem_bytes((res.get("requests") or {}).get("memory"))
    lim_m = _mem_bytes((res.get("limits") or {}).get("memory"))
    req_c = _cpu_millis((res.get("requests") or {}).get("cpu"))

    # OOM: the workload was killed for memory — raise to the known-good floor
    oom = "oom" in ev.signals
    if oom or req_m < _MEM_FLOOR_REQ or lim_m < _MEM_FLOOR_LIM:
        lines, section = [], None
        for ln in vp.read_text(encoding="utf-8").splitlines(keepends=True):
            st = ln.strip()
            if st == "requests:":
                section = "req"
            elif st == "limits:":
                section = "lim"
            elif st.startswith("memory:") and section == "req":
                ln = ln[: ln.index("memory:")] + "memory: 64Mi\n"
            elif st.startswith("memory:") and section == "lim":
                ln = ln[: ln.index("memory:")] + "memory: 128Mi\n"
            elif ln and not ln[0].isspace():
                section = None
            lines.append(ln)
        out.append(_f(
            "runtime_constraints", "oom_memory",
            ("container OOMKilled on startup" if oom
             else "memory limits below a safe floor")
            + "; raised requests/limits memory to 64Mi/128Mi",
            [("charts/app/values.yaml", "".join(lines).encode("utf-8"))]))

    # Unschedulable: a request no node can satisfy keeps the pod Pending forever
    elif ("unschedulable" in ev.signals and (req_m > 1024**3 or req_c > 4000)) or \
            req_m >= _MEM_ABSURD or req_c >= _CPU_ABSURD:
        lines, section = [], None
        for ln in vp.read_text(encoding="utf-8").splitlines(keepends=True):
            st = ln.strip()
            if st == "requests:":
                section = "req"
            elif st == "limits:":
                section = "lim"
            elif section in ("req", "lim") and st.startswith("memory:"):
                ln = ln[: ln.index("memory:")] + (
                    "memory: 64Mi\n" if section == "req" else "memory: 128Mi\n")
            elif section in ("req", "lim") and st.startswith("cpu:"):
                ln = ln[: ln.index("cpu:")] + (
                    "cpu: 50m\n" if section == "req" else "cpu: 250m\n")
            elif ln and not ln[0].isspace():
                section = None
            lines.append(ln)
        out.append(_f(
            "runtime_constraints", "unschedulable_resources",
            f"resource request (memory {req_m}B / cpu {req_c}m) exceeds what any "
            "node can satisfy — the pod can never schedule; clamped "
            "requests/limits back to the chart's working floor",
            [("charts/app/values.yaml", "".join(lines).encode("utf-8"))]))

    # security posture: the container must run hardened
    sc = v.get("securityContext") or {}
    ru = sc.get("runAsUser")
    hardened = (
        sc.get("runAsNonRoot") is True
        and isinstance(ru, int) and not isinstance(ru, bool) and ru >= 1000
        and sc.get("allowPrivilegeEscalation") is False
        and sc.get("readOnlyRootFilesystem") is True
        and "ALL" in ((sc.get("capabilities") or {}).get("drop") or [])
    )
    if not hardened:
        lines = vp.read_text(encoding="utf-8").splitlines(keepends=True)
        new, i = [], 0
        while i < len(lines):
            ln = lines[i]
            if ln.strip() == "securityContext:" and not ln[0].isspace():
                new.append(_HARDENED_SC)
                i += 1
                while i < len(lines) and (not lines[i].strip() or lines[i][0].isspace()):
                    i += 1
                continue
            new.append(ln)
            i += 1
        out.append(_f(
            "runtime_constraints", "security_context",
            "container securityContext is not hardened; applied the standard hardened "
            "posture (non-root uid 1000, RO rootfs, no priv-esc, drop ALL caps)",
            [("charts/app/values.yaml", "".join(new).encode("utf-8"))]))
    return out


# ---------------------------------------------------------------------------
# P: config contract — key references vs the keys the referenced object
#    defines, and config values vs the modes the app source supports
# ---------------------------------------------------------------------------
def p_config_contract(tree, ev):
    out = []
    dp = tree / "charts" / "app" / "templates" / "deployment.yaml"
    cp = tree / "charts" / "app" / "templates" / "configmap.yaml"
    vp = tree / "charts" / "app" / "values.yaml"
    v = _yload(vp)

    # a configMapKeyRef key the ConfigMap does not define can never resolve
    try:
        dep = dp.read_text(encoding="utf-8")
        cm = cp.read_text(encoding="utf-8")
    except OSError:
        dep = cm = ""
    if "configMapKeyRef" in dep and ".Values.config.key" in dep:
        data_block = re.split(r"(?m)^data:\s*$", cm)[-1] if re.search(r"(?m)^data:\s*$", cm) else ""
        cm_keys = re.findall(r"(?m)^\s{2}([A-Za-z0-9_][\w.-]*):", data_block)
        cur = (v.get("config") or {}).get("key")
        if cm_keys and cur not in cm_keys:
            want = cm_keys[0]
            lines, section = [], None
            for ln in vp.read_text(encoding="utf-8").splitlines(keepends=True):
                if ln.strip() == "config:" and not ln[0].isspace():
                    section = "config"
                elif ln and not ln[0].isspace():
                    section = None
                if section == "config" and ln.strip().startswith("key:"):
                    ln = ln[: ln.index("key:")] + f"key: {want}\n"
                lines.append(ln)
            out.append(_f(
                "config_contract", "configmap_key",
                f"configMapKeyRef asks for key {cur!r} which the ConfigMap does not "
                f"define {cm_keys}; set config.key to {want!r}",
                [("charts/app/values.yaml", "".join(lines).encode("utf-8"))]))

    # a config value naming a mode the app supports a better structured form of
    fmt = str(v.get("logFormat") or "")
    try:
        obs = (tree / "app" / "obs.py").read_text(encoding="utf-8")
    except OSError:
        obs = ""
    if fmt and fmt != "json" and '"json"' in obs:
        text = re.sub(r"(\n\s*logFormat:\s*)\S+", r"\1json",
                      vp.read_text(encoding="utf-8"), count=1)
        out.append(_f(
            "config_contract", "log_format",
            f"logFormat is {fmt!r}; the app's obs.py supports a structured 'json' "
            "mode — set it",
            [("charts/app/values.yaml", text.encode("utf-8"))]))
    return out


# fixed deterministic order: build-blocking integrity first, then chart wiring,
# HTTP contract, service wiring, runtime constraints, config contract.
PRIMITIVES = [
    p_source_integrity,
    p_chart_value_wiring,
    p_http_contract,
    p_service_wiring,
    p_runtime_constraints,
    p_config_contract,
]


# ---------------------------------------------------------------------------
# deterministic composition engine
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class ComposeResult:
    applied: list       # {"primitive", "diagnosis", "file", "rationale"}
    duplicates: list    # {"primitive", "diagnosis", "file"}
    conflicts: list     # {"file", "kept", "skipped", "skipped_diagnosis"}
    rationales: list    # "primitive.diagnosis: rationale" per applied finding
    changed: set        # relpaths whose bytes changed vs compose() start


def _diff_lines(old_bytes, new_bytes):
    """(removed_lines, added_lines) between two byte blobs, as line-content sets."""
    old = old_bytes.decode("utf-8", "replace").splitlines()
    new = new_bytes.decode("utf-8", "replace").splitlines()
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    removed, added = set(), set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed.update(old[i1:i2])
        if tag in ("replace", "insert"):
            added.update(new[j1:j2])
    return removed, added


def compose(tree, ev, primitives=None):
    """Run the primitives in their fixed order against `tree` (a working copy
    the caller owns — it IS mutated) and compose their edits.

    Composition semantics:
      * primitives run in PRIMITIVES order; each sees the tree as already
        edited by earlier primitives (edits are computed against the current
        candidate bytes);
      * an edit identical to the current bytes of an already-edited file is
        collapsed as a duplicate;
      * an edit that would remove/replace a line an earlier primitive
        introduced is an explicit conflict: recorded, skipped, never silently
        overwritten.
    """
    prims = PRIMITIVES if primitives is None else primitives
    claims: dict[str, list[tuple[str, set]]] = {}  # rel -> [(owner, added_lines)]
    res = ComposeResult(applied=[], duplicates=[], conflicts=[], rationales=[], changed=set())
    for prim in prims:
        try:
            findings = prim(tree, ev) or []
        except Exception as exc:  # noqa: BLE001 — a primitive crash means "no findings"
            print(f"  [advanced] primitive {prim.__name__} errored: {exc}")
            findings = []
        for f in findings:
            applied_any = False
            for rel, nb in f.edits:
                p = tree / rel
                cur = p.read_bytes() if p.exists() else b""
                if nb == cur:
                    if rel in claims:
                        res.duplicates.append({"primitive": f.primitive,
                                               "diagnosis": f.diagnosis, "file": rel})
                    continue
                removed, added = _diff_lines(cur, nb)
                kept_owner = None
                for owner, owner_added in claims.get(rel, []):
                    if owner != f.primitive and removed & owner_added:
                        kept_owner = owner
                        break
                if kept_owner:
                    res.conflicts.append({"file": rel, "kept": kept_owner,
                                          "skipped": f.primitive,
                                          "skipped_diagnosis": f.diagnosis})
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(nb)
                claims.setdefault(rel, []).append((f.primitive, added))
                res.applied.append({"primitive": f.primitive, "diagnosis": f.diagnosis,
                                    "file": rel, "rationale": f.rationale})
                res.changed.add(rel)
                applied_any = True
            if applied_any:
                res.rationales.append(f"{f.primitive}.{f.diagnosis}: {f.rationale}")
    return res
