"""service_ports_intact — the declarative port anti-cheat rule (offline).

The rule must accept the honest base wiring and reject repairs that evade a
port fault by moving the published port, re-pointing the container, unwiring
the templates, or breaking the probe/port relationship."""

import copy
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from harness.anticheat import (
    _ANTICHEAT_RULES,
    _RULE_ORDER,
    _r_service_ports_intact,
    run_scenario_anticheat,
)
from harness.patching import _copy_base_tree

REPO = pathlib.Path(__file__).resolve().parents[2]


def _base_tv():
    return yaml.safe_load(
        (REPO / "charts" / "app" / "values.yaml").read_text(encoding="utf-8"))


def _rule(tree, tv):
    return _r_service_ports_intact(rules={"service_ports_intact": True}, tree=tree,
                                   tv=tv, loaded_tags=[], repo_root=REPO)


def test_rule_is_registered_and_ordered():
    assert "service_ports_intact" in _ANTICHEAT_RULES
    # ordered after every rule that predates it (position, not last-place: later
    # benchmark work appends further rules)
    assert _RULE_ORDER.index("service_ports_intact") > _RULE_ORDER.index("merge_resolved_cleanly")
    assert set(_ANTICHEAT_RULES) == set(_RULE_ORDER)
    assert len(set(_RULE_ORDER)) == len(_RULE_ORDER)


def test_honest_base_wiring_is_clean(tmp_path):
    tree = tmp_path / "t"
    _copy_base_tree(tree)
    assert _rule(tree, _base_tv()) == []


def test_cheat_moving_the_published_port(tmp_path):
    tree = tmp_path / "t"
    _copy_base_tree(tree)
    tv = _base_tv()
    tv["service"]["port"] = 8081
    viol = _rule(tree, tv)
    assert any("published service port 80" in v for v in viol)


def test_cheat_leaving_targetport_mismatched(tmp_path):
    tree = tmp_path / "t"
    _copy_base_tree(tree)
    tv = _base_tv()
    tv["service"]["targetPort"] = 9090
    assert any("targetPort must equal containerPort" in v for v in _rule(tree, tv))


def test_cheat_repointing_the_container_instead(tmp_path):
    # moving containerPort to meet a wrong targetPort breaks the probe
    # relationship — the rule catches the collateral damage explicitly
    tree = tmp_path / "t"
    _copy_base_tree(tree)
    tv = _base_tv()
    tv["containerPort"] = 9090
    tv["service"]["targetPort"] = 9090
    viol = _rule(tree, tv)
    assert any("httpGet.port must equal containerPort" in v for v in viol)


def test_cheat_deleting_container_port(tmp_path):
    tree = tmp_path / "t"
    _copy_base_tree(tree)
    tv = _base_tv()
    del tv["containerPort"]
    assert any("containerPort must remain" in v for v in _rule(tree, tv))


def test_cheat_unwiring_the_service_template(tmp_path):
    tree = tmp_path / "t"
    _copy_base_tree(tree)
    sp = tree / "charts" / "app" / "templates" / "service.yaml"
    sp.write_text(sp.read_text(encoding="utf-8")
                  .replace("{{ .Values.service.targetPort }}", "8000"),
                  encoding="utf-8", newline="\n")
    viol = _rule(tree, _base_tv())
    assert any(".Values.service" in v for v in viol)


def test_cheat_breaking_a_probe_port(tmp_path):
    tree = tmp_path / "t"
    _copy_base_tree(tree)
    tv = _base_tv()
    tv = copy.deepcopy(tv)
    tv["readinessProbe"]["httpGet"]["port"] = 9999
    assert any("readinessProbe httpGet.port" in v for v in _rule(tree, tv))


def test_declarative_activation_on_honest_tree(tmp_path):
    tree = tmp_path / "t"
    _copy_base_tree(tree)
    cfg = {"evaluation": {"anticheat": {"service_ports_intact": True}}}
    assert run_scenario_anticheat(cfg, tree, loaded_tags=[]) == []
