"""Validation and error redaction for credential fills."""
from __future__ import annotations

import ipaddress
import json
import re
from contextlib import contextmanager
from urllib.parse import urlsplit

from ._pw._impl._errors import Error


def validate_expect_origin(origin: str | None) -> None:
    if origin is None:
        return
    message = (
        "expect_origin must be a serialized origin (scheme://host[:port]), "
        "with no credentials, path, query or fragment; "
        "expect_origin=<invalid>; nothing was written"
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


def redact_fill_value(message: str, value: str) -> str:
    if not value:
        return message
    for spelling in sorted(
        {value, repr(value)[1:-1], json.dumps(value)[1:-1]}, key=len, reverse=True
    ):
        message = message.replace(spelling, "<redacted>")
    return message


def validate_fill_expectations(
    expect_origin: str | None, expect_input_type: str | None
) -> None:
    validate_expect_origin(expect_origin)
    if expect_input_type is None:
        return
    if expect_origin is None:
        raise ValueError(
            "expect_input_type requires expect_origin; "
            "expect_origin=None; nothing was written"
        )
    if not isinstance(expect_input_type, str) or expect_input_type.lower() not in {
        "button", "checkbox", "color", "date", "datetime-local", "email", "file",
        "hidden", "image", "month", "number", "password", "radio", "range",
        "reset", "search", "submit", "tel", "text", "time", "url", "week",
    }:
        raise ValueError(
            "expect_input_type must be a known HTML input type name; "
            "expect_origin=<provided>; nothing was written"
        )


@contextmanager
def protect_fill_value(
    value: str, expect_origin: str | None, *, expect_input_type: str | None = None
):
    validate_fill_expectations(expect_origin, expect_input_type)
    try:
        yield
    except Error as error:
        if expect_origin is None:
            raise
        # Do not retain a driver stack, call log or DOM preview containing secrets.
        raise type(error)(redact_fill_value(str(error), value)) from None
