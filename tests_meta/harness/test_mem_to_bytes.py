"""harness.anticheat._mem_to_bytes (Kubernetes memory quantity parsing)."""

import pytest

from harness.anticheat import _mem_to_bytes

MiB = 1024 ** 2


@pytest.mark.parametrize("value,expected", [
    ("16Mi", 16 * MiB),
    ("64Mi", 64 * MiB),
    ("128Mi", 128 * MiB),
    ("1G", 1_000_000_000),
    ("1Gi", 1024 ** 3),
    ("512", 512),
    ("", 0),
    (None, 0),
    ("garbage", 0),
])
def test_mem_to_bytes(value, expected):
    assert _mem_to_bytes(value) == expected


def test_floor_comparison_semantics():
    # the min_memory rule compares parsed bytes with `<`
    assert _mem_to_bytes("16Mi") < _mem_to_bytes("64Mi")
    assert not (_mem_to_bytes("128Mi") < _mem_to_bytes("128Mi"))
