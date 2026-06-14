import pytest

from app.services.oauth_state import create_oauth_state, verify_oauth_state


def test_oauth_state_round_trip():
    state = create_oauth_state("user-123", "schwab")
    user_id = verify_oauth_state(state, "schwab")
    assert user_id == "user-123"


def test_oauth_state_rejects_wrong_broker():
    state = create_oauth_state("user-123", "tradier")
    with pytest.raises(ValueError):
        verify_oauth_state(state, "schwab")


def test_oauth_state_rejects_tampered():
    state = create_oauth_state("user-123", "schwab")
    with pytest.raises(ValueError):
        verify_oauth_state(state + "x", "schwab")
