"""Held-out leakage guards: the FROZEN repair implementation must contain no
held-out scenario ids or held-out-specific logic, and harness/agents/** must be
byte-identical to the agent freeze commit."""

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

REPO = pathlib.Path(__file__).resolve().parents[2]
AGENT_FREEZE_COMMIT = "8ccbe0d62df1c336e2384d45486db52194630892"
_FORBIDDEN = re.compile(r"\bh0[123]\b|\bvh0[1-8]\b|held[-_ ]?out", re.IGNORECASE)


def _agent_sources():
    return sorted((REPO / "harness" / "agents").glob("*.py"))


def test_agent_sources_exist():
    names = {p.name for p in _agent_sources()}
    assert {"fix_agent.py", "primitives.py", "contracts.py"} <= names


def test_no_held_out_ids_or_terms_in_frozen_agent_code():
    for f in _agent_sources():
        text = f.read_text(encoding="utf-8")
        m = _FORBIDDEN.search(text)
        assert m is None, f"{f.name}: forbidden held-out reference {m.group(0)!r}"


def test_no_scenario_id_to_answer_mapping_beyond_baseline_table():
    # the only scenario-id table allowed in agent code is the documented
    # baseline heuristic table for the ORIGINAL benchmark
    import harness.agents.fix_agent as fa
    import harness.agents.primitives as prims
    import inspect
    prim_src = inspect.getsource(prims)
    assert "scenario-0" not in prim_src  # primitives are scenario-blind
    # scenario-ids appear only in the baseline rules / matrix list, never in a
    # conditional inside the derive path
    derive = inspect.getsource(fa.run) + inspect.getsource(fa._derive_repair)
    assert not re.search(r"scenario_id\s*==\s*['\"]", derive)


# v1's working-tree freeze pin retired when v2 development began (v2 modifies
# agent code by design). The historical guarantee is asserted instead: no
# commit in the v1 evaluation window (freeze -> v1 final) touched agent code.
V1_FINAL_COMMIT = "bd24458ca9c856735f9d776fa5e54eb9f2d91985"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_v1_freeze_history_is_intact():
    cp = subprocess.run(
        ["git", "log", "--oneline",
         f"{AGENT_FREEZE_COMMIT}..{V1_FINAL_COMMIT}", "--", "harness/agents"],
        cwd=REPO, capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.strip() == "", (
        "a commit between the v1 agent freeze and the v1 final commit touched "
        "harness/agents/**:\n" + cp.stdout[:2000])
