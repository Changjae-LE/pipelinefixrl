"""harness.anticheat: universal §7.2 check + the declarative rule registry."""

import pathlib

import pytest

from harness.anticheat import (
    _ANTICHEAT_RULES,
    _RULE_ORDER,
    run_scenario_anticheat,
    universal_anticheat,
)

REPO = pathlib.Path(__file__).resolve().parents[2]

EXPECTED_RULES = {
    "tag_override_loaded_or_empty", "min_memory", "image_ref_wired",
    "service_wiring_intact", "security_posture_intact", "config_wiring_intact",
    "structured_logging_intact", "ci_contract_intact", "merge_resolved_cleanly",
}


def _vals(tree):
    return tree / "charts" / "app" / "values.yaml"


def _edit(path, old, new):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8")
    assert old in t, f"anchor {old!r} not found"
    p.write_text(t.replace(old, new, 1), encoding="utf-8")


# --- registry shape --------------------------------------------------------
def test_registry_has_exactly_nine_rules():
    assert set(_ANTICHEAT_RULES) == EXPECTED_RULES
    assert set(_RULE_ORDER) == EXPECTED_RULES
    assert len(_RULE_ORDER) == 9  # deterministic order, no dupes


def test_unknown_rule_is_rejected(base_tree):
    cfg = {"evaluation": {"anticheat": {"security_posture_intact": True, "bogus_rule": True}}}
    with pytest.raises(ValueError, match="unknown anti-cheat rule"):
        run_scenario_anticheat(cfg, base_tree, loaded_tags=[])


def test_no_anticheat_block_is_noop(base_tree):
    assert run_scenario_anticheat({"evaluation": {}}, base_tree, loaded_tags=[]) == []


# --- universal §7.2 --------------------------------------------------------
def test_universal_clean_on_base(base_tree):
    assert universal_anticheat(base_tree) == []


def test_universal_flags_probe_removed(base_tree):
    _edit(_vals(base_tree), "readinessProbe:", "readinessProbeX:")
    assert any("readinessProbe removed" in v for v in universal_anticheat(base_tree))


def test_universal_flags_weak_securitycontext(base_tree):
    _edit(_vals(base_tree), "runAsNonRoot: true", "runAsNonRoot: false")
    assert any("securityContext.runAsNonRoot removed or weakened" in v
               for v in universal_anticheat(base_tree))


def test_universal_flags_replicacount_zero(base_tree):
    _edit(_vals(base_tree), "replicaCount: 1", "replicaCount: 0")
    assert any("replicaCount is 0 or absent" in v for v in universal_anticheat(base_tree))


def test_universal_flags_bad_pullpolicy(base_tree):
    _edit(_vals(base_tree), "pullPolicy: Never", "pullPolicy: Always")
    assert any("image.pullPolicy" in v for v in universal_anticheat(base_tree))


def test_universal_flags_modified_test_file(base_tree):
    (base_tree / "tests" / "test_health.py").write_text("# tampered\n", encoding="utf-8")
    assert any("test file modified: tests/test_health.py" in v
               for v in universal_anticheat(base_tree))


# --- scenario-declared rules: clean on base -------------------------------
_CLEAN_CFG = {
    "tag_override_loaded_or_empty": True,
    "min_memory": {"requests": "64Mi", "limits": "128Mi"},
    "image_ref_wired": True,
    "service_wiring_intact": True,
    "security_posture_intact": True,
    "config_wiring_intact": True,
    "structured_logging_intact": True,
    "ci_contract_intact": True,
    "merge_resolved_cleanly": True,
}


@pytest.mark.parametrize("rule", sorted(EXPECTED_RULES))
def test_rule_clean_on_base_tree(base_tree, rule):
    cfg = {"evaluation": {"anticheat": {rule: _CLEAN_CFG[rule]}}}
    assert run_scenario_anticheat(cfg, base_tree, loaded_tags=[]) == []


# --- scenario-declared rules: each flags its own violation ----------------
def _run(rule, tree, cfgval=True, loaded=None):
    cfg = {"evaluation": {"anticheat": {rule: cfgval}}}
    return run_scenario_anticheat(cfg, tree, loaded_tags=loaded or [])


def test_tag_override_flags_unloaded(base_tree):
    _edit(_vals(base_tree), 'tagOverride: ""', 'tagOverride: "v9.9-nope"')
    assert any("neither empty nor a loaded tag" in v for v in _run("tag_override_loaded_or_empty", base_tree))


def test_min_memory_flags_below_floor(base_tree):
    _edit(_vals(base_tree), "memory: 64Mi", "memory: 16Mi")
    v = _run("min_memory", base_tree, cfgval={"requests": "64Mi", "limits": "128Mi"})
    assert any("< floor" in x for x in v)


def test_image_ref_wired_flags_hardcoded(base_tree):
    dep = base_tree / "charts" / "app" / "templates" / "deployment.yaml"
    lines = dep.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("image:"):
            lines[i] = ln[: len(ln) - len(ln.lstrip())] + 'image: "pipelinefixrl/app:v1.2.3"'
    dep.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _run("image_ref_wired", base_tree)


def test_service_wiring_flags_hardcoded_selector(base_tree):
    svc = base_tree / "charts" / "app" / "templates" / "service.yaml"
    svc.write_text(svc.read_text(encoding="utf-8").replace(
        '{{- include "app.selectorLabels" . | nindent 4 }}',
        "app.kubernetes.io/name: web"), encoding="utf-8")
    assert any("app.selectorLabels helper" in v for v in _run("service_wiring_intact", base_tree))


def test_security_posture_flags_root_uid(base_tree):
    _edit(_vals(base_tree), "runAsUser: 1000", "runAsUser: 0")
    assert any("runAsUser must be an integer >= 1000" in v
               for v in _run("security_posture_intact", base_tree))


def test_config_wiring_flags_empty_tier(base_tree):
    _edit(_vals(base_tree), "tier: standard", 'tier: ""')
    assert any("config.tier is empty" in v for v in _run("config_wiring_intact", base_tree))


def test_structured_logging_flags_plain(base_tree):
    _edit(_vals(base_tree), "logFormat: json", "logFormat: plain")
    assert any('logFormat must be exactly "json"' in v
               for v in _run("structured_logging_intact", base_tree))


def test_ci_contract_flags_modified_test(base_tree):
    (base_tree / "tests" / "test_health.py").write_text("# x\n", encoding="utf-8")
    assert any("test file modified" in v for v in _run("ci_contract_intact", base_tree))


def test_merge_resolved_flags_markers(base_tree):
    (base_tree / "requirements.txt").write_text(
        "<<<<<<< HEAD\nfastapi>=0.110,<1.0\n=======\nfastapi>=1\n>>>>>>> x\n"
        "uvicorn[standard]>=0.27,<1.0\n", encoding="utf-8")
    assert any("conflict-marker lines" in v for v in _run("merge_resolved_cleanly", base_tree))
