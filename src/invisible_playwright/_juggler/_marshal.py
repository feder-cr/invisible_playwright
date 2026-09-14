"""Turning protocol values into Python ones, and back.

⛔ THESE ARE LEAVES, WHICH IS WHY THEY COULD MOVE AT ALL. Not one of them
mentions a dispatcher class: they take a value and return a value. `server.py`
holds a mutually recursive object graph - a page makes frames, a frame makes
element handles, a handle points back at its frame - and splitting THAT across
modules buys navigation with import cycles, which is a bad trade. These do not
have the problem, and they were scattered BETWEEN the classes: reading
`ElementHandleDispatcher` you fell into `_serialize` on the way to
`FrameDispatcher`.

They are re-exported from `server.py`, so nothing that imported them from
there has to move.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _as_callable(expression: str, argument: Any) -> str:
    """Wrap an expression so it can always be CALLED with the argument.

    <M> PLAYWRIGHT SENDS BOTH FORMS DOWN THE SAME FIELD. `page.evaluate("1+1")`
    and `page.evaluate("() => document.title")` arrive as the same
    `expression` string, and the client does not say which is which. Evaluating
    the raw string works for the first and, for the second, produces a FUNCTION
    OBJECT: no error, no exception, and the caller gets None back for a call
    that looked fine.

    Measured on 2026-08-27: `page.evaluate("() => !!x")` answered None, and the
    assertion that caught it read like the browser had changed behaviour.

    So the string is wrapped and the result called if it turned out to be a
    function. Deciding by shape - does it start with `(` or `function` or
    `async` - is the version that gets it wrong: `(1+2)` starts with a
    parenthesis and is not a function.

    <M> THE ARGUMENT GOES IN HERE, IN THE SAME PASS, AND THAT IS THE WHOLE
    POINT. It used to be spelled `r(ARG)` and the caller substituted it
    afterwards with `.replace("ARG", ...)` - over a string that already held
    the caller's expression. So every uppercase `ARG` a caller wrote was
    rewritten to the argument's JSON, inside their own code, silently.

    Measured on 2026-09-14 by driving it: `'CARGO'.length` answered 6 instead
    of 5, because the source that ran was `'CnullO'.length`; a selector
    `[data-role='TARGET']` became `[data-role='TnullET']` and matched nothing;
    `/ARGH/` became `/nullH/`; and `const ARG = 5` was a SyntaxError. The three
    letters need only appear inside a longer word - MARGIN, LARGE, CHARGE - and
    three of those four failures are silent, which is the bad kind.

    A format string is only scanned where the format string itself is, so the
    expression is an ARGUMENT to `%` and never a place `%` looks. The sibling
    wrapper in `op_eval_on_selector` was already written this way.
    """
    return ("(() => { const r = (%s);"
            "  return typeof r === 'function' ? r(%s) : r; })()"
            % (expression, json.dumps(argument, default=str)))


def _deserialize(value: Any) -> Any:
    """The tagged union Playwright sends for an ARGUMENT, back to a value."""
    if not isinstance(value, dict):
        return value
    if "value" in value and "handles" in value:
        return _deserialize(value["value"])
    for tag, convert in (("n", lambda v: v), ("s", lambda v: v),
                         ("b", lambda v: v)):
        if tag in value:
            return convert(value[tag])
    if "v" in value:
        return {"null": None, "undefined": None, "NaN": float("nan"),
                "Infinity": float("inf"),
                "-Infinity": float("-inf")}.get(value["v"])
    if "a" in value:
        return [_deserialize(x) for x in value["a"]]
    if "o" in value:
        return {e["k"]: _deserialize(e["v"]) for e in value["o"]}
    return None


def _with_argument(params: Dict) -> str:
    """The expression, callable, with the caller's argument placed in it.

    <M> THE ONLY PLACE THAT BUILDS THIS. `wait_for_function` used to repeat the
    two lines inline, which is how one of the two could have been fixed and the
    other left alone.
    """
    return _as_callable(params["expression"], _deserialize(params.get("arg")))


def _js_string(value: str) -> str:
    """A Python string as a JavaScript literal.

    ⛔ `json.dumps` and never manual quoting: the html handed to `set_content`
    is arbitrary, and a single quote, a backslash or a line separator inside it
    would close the literal early. That exact defect - an apostrophe closing a
    single-quoted JavaScript string - broke the driver bundle on 2026-08-24.
    """
    return json.dumps(value)


def _guid_of(value: Any) -> Optional[str]:
    if isinstance(value, dict) and "guid" in value:
        return value["guid"]
    return None


def _serialize(value: Any, counter: Optional[Dict] = None) -> Any:
    """A Python value in the shape `parse_result` expects.

    ⛔ Playwright does not send bare JSON: it sends a tagged union, and
    `_connection.parse_value` reads the TAG. A bare `True` where `{"b": true}`
    is expected does not raise - it falls through to the object branch and comes
    back as something else entirely.
    """
    if counter is None:
        counter = {"n": 0}
    if value is None:
        return {"v": "null"}
    if isinstance(value, bool):
        return {"b": value}
    if isinstance(value, (int, float)):
        return {"n": value}
    if isinstance(value, str):
        return {"s": value}
    if isinstance(value, (list, tuple)):
        # <M> `id` IS MANDATORY on a list and on an object, and its absence is
        # a KeyError deep inside `parse_value`, not a message. Playwright uses
        # it to rebuild cyclic references: every container is registered under
        # its id so a later `{"ref": id}` can point back at it. We never emit a
        # `ref` - the values crossing here come from JSON and cannot be cyclic
        # - but the id has to be there anyway, because the reader indexes on it
        # unconditionally.
        counter["n"] += 1
        return {"a": [_serialize(x, counter) for x in value], "id": counter["n"]}
    if isinstance(value, dict):
        counter["n"] += 1
        return {"o": [{"k": k, "v": _serialize(v, counter)}
                      for k, v in value.items()], "id": counter["n"]}
    return {"s": str(value)}


# ── network ─────────────────────────────────────────────────────────────────
def _headers_array(raw) -> List[Dict[str, str]]:
    """Headers as the ARRAY of pairs the client expects, never a dict.

    ⛔ `RawHeaders` iterates over `[{"name": ..., "value": ...}]` and a dict
    iterates over its KEYS, so handing one over does not raise: it produces a
    header list whose every value is missing, and `response.headers` comes back
    full of empty strings. Juggler already sends the array form; this exists so
    a caller-built dict cannot slip through.
    """
    if isinstance(raw, dict):
        return [{"name": k, "value": str(v)} for k, v in raw.items()]
    out = []
    for entry in raw or []:
        if isinstance(entry, dict) and "name" in entry:
            out.append({"name": entry["name"],
                        "value": str(entry.get("value", ""))})
    return out


def _resource_type(params: Dict) -> str:
    """⛔ Juggler says `cause`, Playwright says `resourceType`, and the two
    vocabularies only partly overlap. An unmapped cause becomes `other`, which
    is what upstream does too - guessing a nicer name would make
    `request.resource_type` disagree with itself between the two transports."""
    cause = (params.get("cause") or params.get("internalCause") or "").lower()
    return {
        "document": "document", "subdocument": "document",
        "stylesheet": "stylesheet", "script": "script",
        "image": "image", "imageset": "image",
        "font": "font", "media": "media",
        "xmlhttprequest": "xhr", "fetch": "fetch",
        "websocket": "websocket", "beacon": "other",
    }.get(cause, "other")


def _button(name: Optional[str]) -> int:
    return {"left": 0, "middle": 1, "right": 2}.get(name or "left", 0)


def _console_text(args: list) -> str:
    """⛔ The console arguments arrive as remote objects, not as text. Reading
    `value` works for a primitive and gives nothing for an object, so the
    preview is used when there is one - which is what the driver shows too."""
    pieces = []
    for a in args:
        if not isinstance(a, dict):
            pieces.append(str(a))
        elif "value" in a:
            pieces.append(str(a["value"]))
        elif a.get("preview"):
            pieces.append(str(a["preview"]))
        elif a.get("unserializableValue"):
            pieces.append(str(a["unserializableValue"]))
        else:
            pieces.append(str(a.get("type") or "object"))
    return " ".join(pieces)


def _location(raw) -> Dict:
    """⛔ ALWAYS a location, never None: `_browser_context.py` casts it and
    falls back only on a missing KEY, so a null here becomes an AttributeError
    somewhere else entirely."""
    raw = raw or {}
    return {"url": raw.get("url") or "",
            "lineNumber": raw.get("lineNumber") or raw.get("line") or 0,
            "columnNumber": raw.get("columnNumber") or raw.get("column") or 0}
