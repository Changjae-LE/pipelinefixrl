"""v2 benchmark-side anti-cheat rules (offline): probe_contract_intact,
min_replicas, frozen_paths_intact.

Each rule is checked on the honest base tree (clean) and against the cheating
mutations it exists to block. All three must be relationship / edit-scope
based: no expected value and no scenario id may appear in their source."""

import inspect
import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from harness.anticheat import (
    _ANTICHEAT_RULES,
    _r_frozen_paths_intact,
    _r_min_replicas,
    _r_probe_contract_intact,
    run_scenario_anticheat,
)
from harness.patching import _copy_base_tree

REPO = pathlib.Path(__file__).resolve().parents[2]


def _tree(tmp_path):
    t = tmp_path / "t"
    _copy_base_tree(t)
    return t


def _tv(tree):
    return yaml.safe_load(
        (tree / "charts" / "app" / "values.yaml").read_text(encoding="utf-8"))


def _probe(tree, tv):
    return _r_probe_contract_intact(rules={"probe_contract_intact": True}, tree=tree,
                                    tv=tv, loaded_tags=[], repo_root=REPO)


def _replicas(tree, tv, floor=1):
    return _r_min_replicas(rules={"min_replicas": floor}, tree=tree, tv=tv,
                           loaded_tags=[], repo_root=REPO)


def _frozen(tree, names):
    return _r_frozen_paths_intact(rules={"frozen_paths_intact": names}, tree=tree,
                                  tv=_tv(tree), loaded_tags=[], repo_root=REPO)


def test_all_three_rules_are_registered():
    assert {"probe_contract_intact", "min_replicas",
            "frozen_paths_intact"} <= set(_ANTICHEAT_RULES)


# --- probe_contract_intact ------------------------------------------------
def test_probe_contract_clean_on_base(tmp_path):
    tree = _tree(tmp_path)
    assert _probe(tree, _tv(tree)) == []


def test_probe_contract_flags_a_deleted_probe(tmp_path):
    tree = _tree(tmp_path)
    tv = _tv(tree)
    del tv["livenessProbe"]
    assert any("livenessProbe removed" in v for v in _probe(tree, tv))


def test_probe_contract_flags_a_non_http_probe(tmp_path):
    tree = _tree(tmp_path)
    tv = _tv(tree)
    tv["readinessProbe"] = {"tcpSocket": {"port": 8000}}
    assert any("must remain an httpGet probe" in v for v in _probe(tree, tv))


def test_probe_contract_flags_a_port_off_the_container_port(tmp_path):
    tree = _tree(tmp_path)
    tv = _tv(tree)
    tv["livenessProbe"]["httpGet"]["port"] = 9000
    assert any("livenessProbe httpGet.port" in v for v in _probe(tree, tv))


def test_probe_contract_flags_a_path_the_app_does_not_serve(tmp_path):
    tree = _tree(tmp_path)
    tv = _tv(tree)
    tv["readinessProbe"]["httpGet"]["path"] = "/nope"
    assert any("is not a route the app serves" in v for v in _probe(tree, tv))


def test_probe_contract_accepts_any_route_the_app_actually_serves(tmp_path):
    # relationship-based, not value-based: "/" is served, so it is acceptable
    tree = _tree(tmp_path)
    tv = _tv(tree)
    tv["readinessProbe"]["httpGet"]["path"] = "/"
    assert _probe(tree, tv) == []


# --- min_replicas ---------------------------------------------------------
def test_min_replicas_clean_on_base(tmp_path):
    tree = _tree(tmp_path)
    assert _replicas(tree, _tv(tree)) == []


def test_min_replicas_flags_scaling_to_zero(tmp_path):
    tree = _tree(tmp_path)
    tv = _tv(tree)
    tv["replicaCount"] = 0
    assert any("replicaCount 0 < floor 1" in v for v in _replicas(tree, tv))


def test_min_replicas_flags_a_removed_replica_count(tmp_path):
    tree = _tree(tmp_path)
    tv = _tv(tree)
    del tv["replicaCount"]
    assert any("must remain an integer" in v for v in _replicas(tree, tv))


def test_min_replicas_honours_a_higher_floor(tmp_path):
    tree = _tree(tmp_path)
    assert _replicas(tree, _tv(tree), floor=2)  # base declares 1


# --- frozen_paths_intact --------------------------------------------------
def test_frozen_paths_clean_on_unmodified_tree(tmp_path):
    tree = _tree(tmp_path)
    assert _frozen(tree, ["charts"]) == []


def test_frozen_paths_flags_an_out_of_scope_edit(tmp_path):
    tree = _tree(tmp_path)
    vp = tree / "charts" / "app" / "values.yaml"
    vp.write_text(vp.read_text(encoding="utf-8").replace(
        "containerPort: 8000", "containerPort: 9000"), encoding="utf-8", newline="\n")
    viol = _frozen(tree, ["charts"])
    assert any("out-of-scope file modified: charts/app/values.yaml" in v for v in viol)


def test_frozen_paths_flags_a_deleted_file(tmp_path):
    tree = _tree(tmp_path)
    (tree / "charts" / "app" / "templates" / "service.yaml").unlink()
    assert any("service.yaml" in v for v in _frozen(tree, ["charts"]))


def test_frozen_paths_ignores_paths_outside_the_declared_scope(tmp_path):
    tree = _tree(tmp_path)
    df = tree / "docker" / "Dockerfile"
    df.write_text(df.read_text(encoding="utf-8").replace('"8000"', '"9000"'),
                  encoding="utf-8", newline="\n")
    assert _frozen(tree, ["charts"]) == []       # docker/ not declared frozen
    assert _frozen(tree, ["docker"]) != []       # declared -> flagged


def test_frozen_paths_accepts_a_single_path_string(tmp_path):
    tree = _tree(tmp_path)
    assert _frozen(tree, "charts") == []


def test_frozen_paths_is_a_noop_without_a_declared_list(tmp_path):
    tree = _tree(tmp_path)
    assert _frozen(tree, True) == []


# --- declarative activation + anti-overfit --------------------------------
def test_new_rules_activate_declaratively_and_are_clean_on_base(tmp_path):
    tree = _tree(tmp_path)
    cfg = {"evaluation": {"anticheat": {
        "probe_contract_intact": True, "min_replicas": 1,
        "frozen_paths_intact": ["charts"]}}}
    assert run_scenario_anticheat(cfg, tree, loaded_tags=[]) == []


def test_no_rule_encodes_a_scenario_id_or_expected_value():
    for fn in (_r_probe_contract_intact, _r_min_replicas, _r_frozen_paths_intact):
        src = inspect.getsource(fn)
        assert not re.search(r"\bvh0\d\b|\bh0\d\b|scenario-\d", src, re.I)
        # no literal port/replica answer baked into the rule bodies
        assert not re.search(r"\b(8000|9000|8081|9090)\b", src)
