"""The IVG API key: how a client presents it and how a server checks it.

Dependency-free on purpose: the HTTP middleware (`api_auth`), the Bolt server, the
SDK client and the CLI all import it, and the client side must not need starlette.

A client sends the key in ``X-Api-Key``. The server key comes from ``IVG_API_KEY``; when that
is unset or empty, authentication is off.
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


def request_is_authorized(headers: Mapping[str, str], expected: str) -> bool:
    """Whether the request's ``X-Api-Key`` header carries the configured key."""
    provided = headers.get(API_KEY_HEADER.lower()) or headers.get(API_KEY_HEADER)
    return key_matches(provided, expected)
