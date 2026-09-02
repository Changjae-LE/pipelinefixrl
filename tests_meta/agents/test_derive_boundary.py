"""Golden-boundary: the advanced derivation path never reads golden material.

Static: the whole primitives module and every derive-path function in
fix_agent contain no 'golden' token in actual code (strings/comments/docstrings
stripped via tokenize), and run() reads golden.patch exactly once (in the
labelled fallback, guarded by allow_golden_fallback).
Runtime: derive for all 10 scenarios with every 'golden' path guarded to raise.
"""

import builtins
import inspect
import io
import pathlib
import token
import tokenize

import harness.agents.fix_agent as fa
import harness.agents.primitives as prims

SIDS = [f"scenario-{n:03d}" for n in range(1, 11)]


def _code_tokens(source):
    """Source with every string literal and comment removed — only real code."""
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type not in (token.STRING, tokenize.COMMENT):
            out.append(tok.string)
    return " ".join(out)


# --- static -------------------------------------------------------------
def test_primitives_module_code_has_no_golden_reference():
    src = pathlib.Path(prims.__file__).read_text(encoding="utf-8")
    assert "golden" not in _code_tokens(src).lower()


def test_derive_path_functions_have_no_golden_reference():
    blob = "".join(inspect.getsource(f) for f in (
        fa._derive_repair, fa._validate_candidate, fa._edits_to_patch,
        fa._latest_broken_run, fa._fresh_broken_run))
    assert "golden" not in _code_tokens(blob).lower()


def test_derive_repair_signature_is_evidence_only():
    assert list(inspect.signature(fa._derive_repair).parameters) == \
        ["scenario_id", "broken_run_dir"]


def test_run_reads_golden_patch_exactly_once_in_fallback():
    src = inspect.getsource(fa.run)
    assert src.count('_load_cfg(scenario_id)["patches"]["golden"]') == 1
    assert src.index("explicit fallback") < src.index('["patches"]["golden"]')
    # the fallback-disabled return happens before the only golden read
    assert src.index("if not allow_golden_fallback:") < src.index('["patches"]["golden"]')


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
