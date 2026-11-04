"""The IVG API key: how a client presents it and how a server checks it.

Dependency-free on purpose: the HTTP middleware (`api_auth`), the Bolt server, the
SDK client and the CLI all import it, and the client side must not need starlette.

A client sends the key in ``X-Api-Key``, which every IVG server reads. A server accepts
it there or as ``Authorization: Bearer <key>`` (what clients before 4.6.0 sent, and what
tools such as Prometheus send). The server key comes from ``IVG_API_KEY``; when that is
unset or empty, authentication is off.
"""
from __future__ import annotations

import hmac
import os
from typing import Any, Mapping

API_KEY_HEADER = "X-Api-Key"
API_KEY_ENV = "IVG_API_KEY"


def configured_key() -> str:
    """The server's key, or ``""`` when authentication is off."""
    return os.environ.get(API_KEY_ENV, "")


def key_matches(provided: Any, expected: str) -> bool:
    """Constant-time comparison of a presented key with the configured one.

    Compares UTF-8 bytes: ``hmac.compare_digest`` raises on non-ASCII ``str``. A
    non-string or empty credential never matches.
    """
    if not expected or not isinstance(provided, str) or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def presented_keys(headers: Mapping[str, str]) -> list[str]:
    """Candidate keys in a request's headers: ``X-Api-Key``, then a Bearer token."""
    out = []
    direct = headers.get(API_KEY_HEADER.lower()) or headers.get(API_KEY_HEADER)
    if direct:
        out.append(direct)
    scheme, _, token = (headers.get("authorization") or "").partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token:
        out.append(token)
    return out


def request_is_authorized(headers: Mapping[str, str], expected: str) -> bool:
    """Whether either header carries the configured key."""
    # Compare every candidate, so the time taken does not say which header held a key.
    results = [key_matches(k, expected) for k in presented_keys(headers)]
    return any(results)


def client_headers(api_key: str | None) -> dict[str, str]:
    """Headers a client sends to present ``api_key``, or ``IVG_API_KEY`` from the client's environment when
    ``api_key`` is None (none when neither is set). Before 4.6.0 R222 `IVGClient` ignored the environment."""
    if api_key is None:
        api_key = os.environ.get(API_KEY_ENV)
    return {API_KEY_HEADER: api_key} if api_key else {}
