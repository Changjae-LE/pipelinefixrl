"""Golden-boundary: the advanced derivation path never reads golden material.

Static: _derive_repair / detectors / helpers contain no 'golden' reference, and
run() reads golden.patch exactly once (in the labelled fallback).
Runtime: derive for all 10 scenarios with every 'golden' path guarded to raise.
"""

import builtins
import inspect
import pathlib

import harness.agents.fix_agent as fa

SIDS = [f"scenario-{n:03d}" for n in range(1, 11)]


# --- static -------------------------------------------------------------
def test_derive_repair_code_has_no_golden_reference():
    src = inspect.getsource(fa._derive_repair)
    code = src.split('"""', 2)[-1]  # strip the docstring; check only code
    assert "golden" not in code


def test_detectors_and_helpers_have_no_golden_reference():
    blob = "".join(inspect.getsource(f) for f in fa._DETECTORS)
    blob += "".join(inspect.getsource(f) for f in (
        fa._yload, fa._mem_bytes, fa._routes, fa._ev_text, fa._conflict_files,
        fa._resolve_conflict_keep_head, fa._norm, fa._edits_to_patch))
    assert "golden" not in blob


def test_derive_repair_signature_is_evidence_only():
    assert list(inspect.signature(fa._derive_repair).parameters) == \
        ["scenario_id", "broken_run_dir"]


def test_run_reads_golden_patch_exactly_once_in_fallback():
    src = inspect.getsource(fa.run)
    assert src.count('_load_cfg(scenario_id)["patches"]["golden"]') == 1
    assert src.index("explicit fallback") < src.index('["patches"]["golden"]')


# --- runtime: derive with all 'golden' paths unreadable ----------------
def test_runtime_derivation_touches_no_golden_path(broken_tree, monkeypatch):
    real_rt = pathlib.Path.read_text
    real_rb = pathlib.Path.read_bytes
    real_open = builtins.open

    def guard(fn):
        def _w(self_or_file, *a, **k):
            if "golden" in str(self_or_file).lower():
                raise AssertionError(f"BOUNDARY VIOLATION: derivation touched {self_or_file}")
            return fn(self_or_file, *a, **k)
        return _w

    monkeypatch.setattr(pathlib.Path, "read_text", guard(real_rt))
    monkeypatch.setattr(pathlib.Path, "read_bytes", guard(real_rb))
    monkeypatch.setattr(builtins, "open", guard(real_open))

    for sid in SIDS:
        d = broken_tree(sid)
        edits, rationale = fa._derive_repair(sid, d)
        assert edits, f"{sid}: derivation produced nothing"
