from __future__ import annotations

import pytest

from lidarr_similar import auth


@pytest.fixture(autouse=True)
def reset_sessions():
    auth._sessions.clear()
    yield
    auth._sessions.clear()


def test_create_session_returns_unique_valid_tokens():
    token_a = auth.create_session()
    token_b = auth.create_session()

    assert token_a != token_b
    assert auth.is_valid_session(token_a)
    assert auth.is_valid_session(token_b)


def test_is_valid_session_rejects_unknown_or_missing_token():
    assert not auth.is_valid_session("not-a-real-token")
    assert not auth.is_valid_session(None)
    assert not auth.is_valid_session("")


def test_destroy_session_revokes_token():
    token = auth.create_session()
    assert auth.is_valid_session(token)

    auth.destroy_session(token)

    assert not auth.is_valid_session(token)


def test_check_password_matches_and_rejects():
    assert auth.check_password("correct-horse", "correct-horse")
    assert not auth.check_password("wrong", "correct-horse")


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("192.168.1.50", True),
        ("10.0.0.5", True),
        ("172.16.0.9", True),
        ("::1", True),
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("not-an-ip", False),
        ("", False),
    ],
)
def test_is_local_address(host, expected):
    assert auth.is_local_address(host) is expected


def test_client_address_prefers_x_forwarded_for():
    headers = {"x-forwarded-for": "192.168.1.50, 10.0.0.1"}
    assert auth.client_address(headers, "172.17.0.1") == "192.168.1.50"


def test_client_address_falls_back_to_direct_host():
    assert auth.client_address({}, "192.168.1.50") == "192.168.1.50"
    assert auth.client_address({}, None) == ""
