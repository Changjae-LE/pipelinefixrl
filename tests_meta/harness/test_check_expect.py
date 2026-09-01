"""harness.scenario._check_expect — expectation evaluation."""

from harness.scenario import _check_expect

CHECKS = [
    {"id": "helm_release_ok", "result": "PASS"},
    {"id": "rollout_complete", "result": "PASS"},
    {"id": "structured_logs_ok", "result": "FAIL"},
]


def test_empty_expect_no_problems():
    assert _check_expect(CHECKS, 65, {}, []) == []


def test_must_pass_hit_and_miss():
    assert _check_expect(CHECKS, 65, {"must_pass": ["helm_release_ok"]}, []) == []
    p = _check_expect(CHECKS, 65, {"must_pass": ["structured_logs_ok"]}, [])
    assert p and "expected PASS, got FAIL" in p[0]


def test_must_fail_hit_and_miss():
    assert _check_expect(CHECKS, 65, {"must_fail": ["structured_logs_ok"]}, []) == []
    p = _check_expect(CHECKS, 65, {"must_fail": ["helm_release_ok"]}, [])
    assert p and "expected FAIL, got PASS" in p[0]


def test_score_max_boundary():
    assert _check_expect(CHECKS, 65, {"score_max": 65}, []) == []          # equal ok
    p = _check_expect(CHECKS, 66, {"score_max": 65}, [])
    assert p and "score 66 > score_max 65" in p[0]


def test_score_min_boundary():
    assert _check_expect(CHECKS, 100, {"score_min": 100}, []) == []
    p = _check_expect(CHECKS, 99, {"score_min": 100}, [])
    assert p and "score 99 < score_min 100" in p[0]


def test_anticheat_clean_gate():
    assert _check_expect(CHECKS, 100, {"anticheat_clean": True}, []) == []
    p = _check_expect(CHECKS, 100, {"anticheat_clean": True}, ["v1"])
    assert p and "anti-cheat violations present" in p[0]
    # not gated unless anticheat_clean is set
    assert _check_expect(CHECKS, 100, {}, ["v1"]) == []
