"""Endpoint-contract reasoning (v2 — Option B on the minimal graph seed).

A consumer failure that names the endpoint it expected is first-class evidence
of a contract. This module turns such evidence, plus the tree's own endpoint
declarations, into typed facts and reconciles them deterministically:

    runtime/tree evidence -> typed fact extraction -> Declaration/Expectation
    records -> deterministic reconciliation -> Reconciliation (consumed by the
    p_consumer_contract primitive, which turns it into ordinary Findings/edits)

Design rules (fixed):
  * parsers extract FACTS only — they never decide repairs;
  * every repair decision lives in `reconcile`, which is generic and
    deterministic;
  * ambiguous evidence, insufficient evidence, unresolved resource identity,
    or conflicting equally-attested expectations all mean NO CHANGE;
  * duplicate evidence must not increase attestation (attestation is the set
    of distinct expected values, never an occurrence count);
  * a current declaration value with independent structured tree-side support
    (a corroborating reference) is never rewritten by evidence alone.

Anti-overfit rule: every extraction pattern below is anchored to a documented
Kubernetes / kubectl error format — never to any benchmark scenario's output.
Nothing in this module may reference a scenario id.
"""

from __future__ import annotations

import dataclasses
import re

import yaml

_PORT_MIN, _PORT_MAX = 1, 65535


def _yload(path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


# ---------------------------------------------------------------------------
# typed facts
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class Declaration:
    """The tree provides an endpoint attribute (producer side)."""
    kind: str        # e.g. "Service"
    attribute: str   # e.g. "port"
    value: object
    source: str      # where in the tree it is declared


@dataclasses.dataclass(frozen=True)
class Expectation:
    """A consumer referenced/attempted an endpoint attribute (consumer side)."""
    kind: str
    name: str        # resource name the consumer addressed ("" if unknown)
    attribute: str
    value: object
    source: str      # the evidence line that produced this fact
    authoritative: bool  # the error format names both the resource and the value


@dataclasses.dataclass(frozen=True)
class Reconciliation:
    """A decided repair: one declaration reconciled to the consumed contract."""
    kind: str
    attribute: str
    current: object
    expected: object
    decl_source: str
    evidence_source: str


# ---------------------------------------------------------------------------
# fact extraction — parsers only, no repair decisions
# ---------------------------------------------------------------------------
# Documented kubectl error formats (kubectl port-forward service resolution).
_EXPECTATION_PATTERNS = (
    # `error: Service <name> does not have a service port <n>` (numeric form)
    (re.compile(r"[Ss]ervice\s+\"?([A-Za-z0-9][A-Za-z0-9._-]*)\"?\s+"
                r"does not have a service port\s+(\d{1,5})\b"),
     "Service", "port", True),
)


def extract_expectations(ev) -> list[Expectation]:
    """Typed consumer expectations from a run's own evidence (failed-check
    reasons, events, logs). Facts only."""
    blob = "\n".join(
        [ev.text] + [f"{cid}: {reason}" for cid, reason in ev.failed_checks])
    out: list[Expectation] = []
    for pat, kind, attr, auth in _EXPECTATION_PATTERNS:
        for m in pat.finditer(blob):
            try:
                value = int(m.group(2))
            except ValueError:
                continue
            if not (_PORT_MIN <= value <= _PORT_MAX):
                continue
            out.append(Expectation(kind=kind, name=m.group(1), attribute=attr,
                                   value=value,
                                   source=f"consumer evidence {m.group(0)!r}",
                                   authoritative=auth))
    return out


def extract_declarations(tree) -> list[Declaration]:
    """Endpoint declarations the tree provides. Facts only."""
    out: list[Declaration] = []
    vp = tree / "charts" / "app" / "values.yaml"
    svc = (_yload(vp).get("service")) or {}
    port = svc.get("port")
    if isinstance(port, int) and not isinstance(port, bool):
        out.append(Declaration(kind="Service", attribute="port", value=port,
                               source="charts/app/values.yaml:service.port"))
    return out


def service_resource_count(tree) -> int:
    """How many Service resources the chart renders — resource identity is only
    resolvable when there is exactly one."""
    n = 0
    tdir = tree / "charts" / "app" / "templates"
    if tdir.is_dir():
        for f in sorted(tdir.glob("*.yaml")):
            try:
                n += f.read_text(encoding="utf-8").count("kind: Service")
            except OSError:
                pass
    return n


def tree_reference_values(tree, kind: str, attribute: str) -> set:
    """Structured tree-side references to an endpoint attribute — independent
    corroboration for a declared value. For (Service, port): Kubernetes
    Ingress backend schema (`backend.service.port.number`, networking.k8s.io/v1)
    and the legacy `servicePort:` field."""
    vals: set = set()
    if (kind, attribute) != ("Service", "port"):
        return vals
    tdir = tree / "charts" / "app" / "templates"
    if not tdir.is_dir():
        return vals
    for f in sorted(tdir.glob("*.yaml")):
        try:
            txt = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in re.finditer(r"^\s*servicePort:\s*(\d{1,5})\s*$", txt, re.M):
            vals.add(int(m.group(1)))
        # networking.k8s.io/v1 Ingress: backend: service: port: number: <n>
        for m in re.finditer(r"^\s*number:\s*(\d{1,5})\s*$", txt, re.M):
            vals.add(int(m.group(1)))
    return vals


# ---------------------------------------------------------------------------
# deterministic reconciliation — the only place repair decisions are made
# ---------------------------------------------------------------------------
def reconcile(declarations, expectations, *, service_count, tree_refs) -> list[Reconciliation]:
    out: list[Reconciliation] = []
    # group authoritative expectations by contract target; attestation is the
    # set of DISTINCT values (duplicates collapse, never amplify)
    targets: dict[tuple, dict] = {}
    for e in expectations:
        if not e.authoritative:
            continue  # weak evidence never rewrites a declaration
        t = targets.setdefault((e.kind, e.attribute), {"values": set(), "sources": {}})
        t["values"].add(e.value)
        t["sources"].setdefault(e.value, e.source)
    for (kind, attribute), t in sorted(targets.items()):
        if len(t["values"]) != 1:
            continue  # conflicting equally-attested expectations -> NO CHANGE
        expected = next(iter(t["values"]))
        matching = [d for d in declarations
                    if d.kind == kind and d.attribute == attribute]
        if len(matching) != 1:
            continue  # no / ambiguous declaration -> NO CHANGE
        if kind == "Service" and service_count != 1:
            continue  # resource identity unresolved -> NO CHANGE
        decl = matching[0]
        if decl.value == expected:
            continue  # contract already satisfied
        if decl.value in tree_refs:
            continue  # current value independently corroborated -> NO CHANGE
        out.append(Reconciliation(kind=kind, attribute=attribute,
                                  current=decl.value, expected=expected,
                                  decl_source=decl.source,
                                  evidence_source=t["sources"][expected]))
    return out


def diagnose(tree, ev) -> list[Reconciliation]:
    """extract facts -> reconcile. Returns decided repairs only."""
    decls = extract_declarations(tree)
    exps = extract_expectations(ev)
    if not decls or not exps:
        return []
    return reconcile(
        decls, exps,
        service_count=service_resource_count(tree),
        tree_refs=tree_reference_values(tree, "Service", "port"),
    )
