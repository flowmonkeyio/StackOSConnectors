import pytest

from stackos_connectors import CallOptions


def test_default_timeout_defers_to_provider():
    assert CallOptions().timeout is None
    assert CallOptions(timeout=None).timeout is None


@pytest.mark.parametrize("seconds", [0.25, 30, 60, 120])
def test_numeric_timeout_is_preserved(seconds):
    assert CallOptions(timeout=seconds).timeout == seconds


@pytest.mark.parametrize("seconds", [0, -1])
def test_nonpositive_timeout_is_rejected(seconds):
    with pytest.raises(ValueError, match="positive"):
        CallOptions(timeout=seconds)
