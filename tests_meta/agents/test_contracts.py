"""v2 consumer-contract reasoning (offline): fact extraction, deterministic
reconciliation, and the required regression/architecture cases — including the
h03-class positive (repaired ONLY because the generic reasoning supports it;
no scenario id appears anywhere in the implementation or these fixtures)."""

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import harness.agents.contracts as con
import harness.agents.primitives as prims
from harness.patching import _copy_base_tree

# the documented kubectl port-forward error format (the fact source; the same
# format the harness's health probe surfaces in a failed-check reason)
KUBECTL_MSG = "port-forward exited early: error: Service app does not have a service port 80"


class FakeEv:
    def __init__(self, text="", failed_checks=()):
        self.text = text
        self.failed_checks = list(failed_checks)
        self.sources = ["checks.json"] if failed_checks else (["events.txt"] if text else [])
        self.signals = set()


def _tree(tmp_path, service_port=None):
    tree = tmp_path / "tree"
    _copy_base_tree(tree)
    if service_port is not None:
        vp = tree / "charts/app/values.yaml"
        txt = vp.read_text(encoding="utf-8")
        assert "  port: 80\n" in txt
        vp.write_text(txt.replace("  port: 80\n", f"  port: {service_port}\n"),
                      encoding="utf-8", newline="\n")
    return tree


def _vals(tree):
    return yaml.safe_load((tree / "charts/app/values.yaml").read_text(encoding="utf-8"))


# --- A. positive regression (h03-class fault, generically derived) ---------
def test_consumer_evidence_repairs_a_mispublished_service_port(tmp_path):
    tree = _tree(tmp_path, service_port=8081)
    ev = FakeEv(failed_checks=[("http_health_ok", KUBECTL_MSG)])
    fs = prims.p_consumer_contract(tree, ev)
    assert len(fs) == 1
    f = fs[0]
    assert f.primitive == "consumer_contract" and f.diagnosis == "published_contract"
    (rel, nb), = f.edits
    (tree / rel).write_bytes(nb)
    v = _vals(tree)
    assert v["service"]["port"] == 80
    assert v["service"]["targetPort"] == 8000  # untouched
    # provenance cites the consumer evidence verbatim
    assert "does not have a service port 80" in f.rationale


def test_expectation_extraction_is_facts_only():
    exps = con.extract_expectations(FakeEv(failed_checks=[("http_health_ok", KUBECTL_MSG)]))
    assert len(exps) == 1
    e = exps[0]
    assert (e.kind, e.name, e.attribute, e.value) == ("Service", "app", "port", 80)
    assert e.authoritative is True


# --- B. healthy negative ---------------------------------------------------
def test_healthy_tree_with_no_failure_evidence_yields_zero_findings(tmp_path):
    tree = _tree(tmp_path)
    assert prims.p_consumer_contract(tree, FakeEv()) == []
    assert con.diagnose(tree, FakeEv()) == []


def test_satisfied_contract_yields_zero_findings_even_with_evidence(tmp_path):
    # declaration already equals the consumed contract -> nothing to do
    tree = _tree(tmp_path)  # port stays 80
    ev = FakeEv(failed_checks=[("http_health_ok", KUBECTL_MSG)])
    assert prims.p_consumer_contract(tree, ev) == []


# --- C. corroborated current value ----------------------------------------
def test_tree_corroborated_value_is_not_rewritten(tmp_path):
    tree = _tree(tmp_path, service_port=8081)
    # an Ingress backend (networking.k8s.io/v1 schema) independently references
    # the current published port — evidence alone must not override the tree
    (tree / "charts/app/templates/ingress.yaml").write_text(
        "apiVersion: networking.k8s.io/v1\n"
        "kind: Ingress\n"
        "metadata:\n  name: app\n"
        "spec:\n  rules:\n    - http:\n        paths:\n"
        "          - path: /\n            pathType: Prefix\n"
        "            backend:\n              service:\n"
        "                name: app\n                port:\n"
        "                  number: 8081\n",
        encoding="utf-8", newline="\n")
    ev = FakeEv(failed_checks=[("http_health_ok", KUBECTL_MSG)])
    assert prims.p_consumer_contract(tree, ev) == []


# --- D. ambiguous evidence -------------------------------------------------
def test_evidence_without_a_named_value_yields_no_repair(tmp_path):
    tree = _tree(tmp_path, service_port=8081)
    # weak signals (no format naming resource+expected value) never rewrite
    ev = FakeEv(text="connection refused while probing the service")
    assert con.extract_expectations(ev) == []
    assert prims.p_consumer_contract(tree, ev) == []


def test_out_of_range_port_value_is_not_a_fact(tmp_path):
    ev = FakeEv(text="error: Service app does not have a service port 99999")
    assert con.extract_expectations(ev) == []


# --- E. conflicting expectations ------------------------------------------
def test_conflicting_equally_attested_expectations_yield_no_repair(tmp_path):
    tree = _tree(tmp_path, service_port=8081)
    ev = FakeEv(failed_checks=[
        ("http_health_ok", "error: Service app does not have a service port 80"),
        ("other_probe", "error: Service app does not have a service port 8080"),
    ])
    assert len(con.extract_expectations(ev)) == 2
    assert prims.p_consumer_contract(tree, ev) == []


# --- F. duplicate evidence -------------------------------------------------
def test_duplicate_evidence_does_not_amplify_attestation(tmp_path):
    tree = _tree(tmp_path, service_port=8081)
    # the same expectation repeated must behave exactly like one occurrence:
    # with a conflicting second value present, duplicates of the first value
    # must NOT outvote it into a repair
    ev = FakeEv(failed_checks=[
        ("a", "error: Service app does not have a service port 80"),
        ("b", "error: Service app does not have a service port 80"),
        ("c", "error: Service app does not have a service port 80"),
        ("d", "error: Service app does not have a service port 8080"),
    ])
    assert prims.p_consumer_contract(tree, ev) == []
    # and duplicates alone still repair exactly once
    ev2 = FakeEv(failed_checks=[
        ("a", KUBECTL_MSG), ("b", KUBECTL_MSG)])
    assert len(prims.p_consumer_contract(tree, ev2)) == 1


# --- G. resource identity --------------------------------------------------
def test_multiple_service_resources_block_the_repair(tmp_path):
    tree = _tree(tmp_path, service_port=8081)
    # a second Service in the chart makes "which Service?" unresolvable from
    # the values-level declaration -> NO CHANGE
    (tree / "charts/app/templates/service-extra.yaml").write_text(
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: app-extra\n"
        "spec:\n  ports:\n    - port: 9999\n",
        encoding="utf-8", newline="\n")
    ev = FakeEv(failed_checks=[("http_health_ok", KUBECTL_MSG)])
    assert prims.p_consumer_contract(tree, ev) == []


# --- H. anti-overfit -------------------------------------------------------
def test_no_scenario_identifiers_in_contract_reasoning():
    import re
    src = pathlib.Path(con.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bh0\d\b|held[-_ ]?out|scenario-\d", src, re.I)
    # and no hard-coded 8081->80 rule: the faulty value must not appear at all
    assert "8081" not in src


def test_reconciliation_is_the_only_decision_point():
    import inspect
    for fn in (con.extract_expectations, con.extract_declarations,
               con.tree_reference_values, con.service_resource_count):
        src = inspect.getsource(fn)
        assert "Reconciliation(" not in src  # parsers never decide repairs
