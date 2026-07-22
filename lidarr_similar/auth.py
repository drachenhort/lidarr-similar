"""Session tracking and local-address detection for the optional login page.

Sessions live in an in-memory set, not a database table - simple, and this app
runs as a single process/single worker (see Dockerfile's plain `uvicorn` CMD),
so there's no cross-process consistency to worry about. The tradeoff is that
restarting the container logs everyone out, which is an acceptable cost for a
self-hosted single-user tool.
"""

from __future__ import annotations

import ipaddress
import secrets

SESSION_COOKIE_NAME = "lidarr_similar_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days - a self-hosted tool, not a bank

_sessions: set[str] = set()


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions.add(token)
    return token


def is_valid_session(token: str | None) -> bool:
    return bool(token) and token in _sessions


def destroy_session(token: str | None) -> None:
    _sessions.discard(token)


def check_password(candidate: str, expected: str) -> bool:
    """Constant-time comparison - a plain `==` leaks timing information about
    how many leading characters matched, which matters for a password check."""
    return secrets.compare_digest(candidate, expected)


def is_local_address(host: str) -> bool:
    """True for loopback/private/link-local addresses (RFC 1918, 127.0.0.0/8, etc.) -
    used to skip the login page for same-machine or same-LAN requests when enabled."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def client_address(headers: dict, direct_host: str | None) -> str:
    """Prefers X-Forwarded-For's first hop over the direct connection's address, since
    this app is commonly run behind a reverse proxy (Unraid, Docker) where the direct
    peer would otherwise always be the proxy itself rather than the real client."""
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return direct_host or ""
