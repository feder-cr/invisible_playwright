"""Validation and error redaction for login autofill."""
from __future__ import annotations

import ipaddress
import json
import re
from contextlib import contextmanager
from urllib.parse import urlsplit


def validate_expect_origin(origin: str) -> None:
    message = (
        "origin must be a serialized origin (scheme://host[:port]), "
        "with no credentials, path, query or fragment; nothing was written"
    )
    if not isinstance(origin, str) or not re.fullmatch(
        r"[a-z][a-z0-9+.-]*://(?:\[[0-9a-f:.]+\]|[a-z0-9._-]+)"
        r"(?::(?:0|[1-9][0-9]*))?", origin
    ):
        raise ValueError(message)
    try:
        parsed = urlsplit(origin)
        port = parsed.port
        host = parsed.hostname
        if parsed.scheme in {"file", "data", "about", "javascript", "blob"}:
            raise ValueError(message)
        if not host or port == {"http": 80, "https": 443, "ws": 80,
                                "wss": 443, "ftp": 21}.get(parsed.scheme, -1):
            raise ValueError(message)
        if ":" in host:
            if str(ipaddress.IPv6Address(host)) != host:
                raise ValueError(message)
        elif re.fullmatch(r"[0-9.]+", host):
            if str(ipaddress.IPv4Address(host)) != host:
                raise ValueError(message)
    except ValueError:
        raise ValueError(message) from None


def validate_autofill_login(
    origin: str, username: str | None, username_selector: str | None,
    username_type: str | None, password: str | None, password_selector: str | None,
) -> None:
    validate_expect_origin(origin)
    if username is None and password is None:
        raise ValueError("autofill_login requires at least one field; nothing was written")
    for name, value, selector in (
        ("username", username, username_selector), ("password", password, password_selector)
    ):
        if value is None and selector is None:
            continue
        if not isinstance(value, str) or not isinstance(selector, str) or not selector:
            raise ValueError(
                f"{name} requires a string value and a nonempty selector; nothing was written")
    if username_type is not None and (
        username is None or not isinstance(username_type, str)
        or username_type.lower() not in {"text", "email", "tel", "url", "search"}
    ):
        raise ValueError(
            "username_type requires a username and a non-password text input type; "
            "nothing was written")


def redact_fill_value(message: str, *values: str | None) -> str:
    spellings = {
        spelling for value in values if isinstance(value, str) and value
        for spelling in (value, repr(value)[1:-1], json.dumps(value)[1:-1],
                         json.dumps(value, ensure_ascii=False)[1:-1])
    }
    if not spellings:
        return message
    return re.sub("|".join(re.escape(s) for s in sorted(spellings, key=len, reverse=True)),
                  lambda _: "<redacted>", message)


@contextmanager
def protect_autofill_values(username: str | None, password: str | None):
    try:
        yield
    except Exception as error:
        # Drop driver stacks, call logs and chained errors that can echo arguments.
        raise type(error)(redact_fill_value(str(error), username, password)) from None
