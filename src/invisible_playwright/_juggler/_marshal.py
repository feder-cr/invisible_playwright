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
import re
from typing import Any, Dict, List, Optional


def _js_number(value: Any) -> str:
    """A number as JavaScript prints it in a template literal: `111`, never
    Python's `111.0` (the timeout of an expect's call log, [B341])."""
    number = float(value)
    return str(int(number)) if number.is_integer() else repr(number)


def _caller_timeout(params: Dict, default_ms: float = 30000) -> Optional[float]:
    """The caller's timeout off the wire, in seconds; None is no limit.

    Milliseconds on the wire. `0` is no limit in Playwright, and until 0.34.0
    it was read as `params.get("timeout") or 30000`, in four places, so a
    `click(timeout=0)` gave up at 30 s and a `launch(timeout=0)` at 60 (B278,
    B276). Absent, the default the operation has when nobody says.
    """
    wanted = params.get("timeout")
    if wanted == 0:
        return None
    if isinstance(wanted, (int, float)) and wanted > 0:
        return float(wanted) / 1000.0
    return default_ms / 1000.0


#: What JavaScript's `String.prototype.trim` removes, WhiteSpace and
#: LineTerminator, and nothing else: Python's `str.strip()` also removes U+0085
#: and U+001C-U+001F, and keeps U+FEFF.
_JS_WHITESPACE = ("\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004"
                  "\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f"
                  "\u205f\u3000\ufeff")


def _as_script(expression: str) -> str:
    """The caller's text as Playwright evaluates it ([B340]).

    Playwright's server passes `page.evaluate`'s text through
    `normalizeEvaluationExpression` and its utility script runs it with
    `globalThis.eval`: the text is a SCRIPT, so a statement with its semicolon
    is fine and the value is the script's completion value, and when that
    value is a function it is called with the argument (`_run_callers_code`
    in `server.py`). The client sends both forms down the same field without
    saying which, so the function is recognised by what the script produced,
    never by its shape: `(1+2)` starts with a parenthesis and is not one.

    ⛔ UNTIL [B340] THE TEXT WAS PUT INSIDE AN EXPRESSION, `const r = (TEXT);`,
    so that its result could be called in the same pass, and an expression is
    all a pair of parentheses holds: `setTimeout(...);` died with "missing ) in
    parenthetical" where Playwright runs it. And the page could see that
    scaffolding around the caller's code. Nothing is added now; the text is
    normalised as Playwright normalises it when the client does not say
    whether it is a function: trimmed as JavaScript trims, and a `function` or
    `async function` DECLARATION put in parentheses, so that it is a value
    rather than a statement whose completion value is undefined.

    ⛔ AND THE ARGUMENT IS NOT IN THE TEXT. It used to be written into the
    wrapper, once as a placeholder that a `.replace` then hunted for inside the
    caller's own code (`'CARGO'.length` answered 6, measured 2026-09-14). It is
    a protocol argument of the call now, so there is nothing to find.
    """
    text = expression.strip(_JS_WHITESPACE)
    if re.match(r"(async)?\s*function(\s|\()", text):
        text = "(" + text + ")"
    return text


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


#: Juggler's `cause` and `internalCause` are `nsIContentPolicy` constant names
#: (`causeTypeToString` in NetworkObserver.js), so they arrive as
#: `TYPE_XMLHTTPREQUEST`, never `xmlhttprequest`. This is the table the
#: Playwright driver's ffNetworkManager uses for the same event.
_CAUSE_TO_RESOURCE_TYPE = {
    "TYPE_INVALID": "other", "TYPE_OTHER": "other",
    "TYPE_SCRIPT": "script", "TYPE_IMAGE": "image",
    "TYPE_STYLESHEET": "stylesheet", "TYPE_OBJECT": "other",
    "TYPE_DOCUMENT": "document", "TYPE_SUBDOCUMENT": "document",
    "TYPE_REFRESH": "document", "TYPE_XBL": "other", "TYPE_PING": "other",
    "TYPE_XMLHTTPREQUEST": "xhr", "TYPE_OBJECT_SUBREQUEST": "other",
    "TYPE_DTD": "other", "TYPE_FONT": "font", "TYPE_MEDIA": "media",
    "TYPE_WEBSOCKET": "websocket", "TYPE_CSP_REPORT": "other",
    "TYPE_XSLT": "other", "TYPE_BEACON": "other", "TYPE_FETCH": "fetch",
    "TYPE_IMAGESET": "image", "TYPE_WEB_MANIFEST": "manifest",
}
_INTERNAL_CAUSE_TO_RESOURCE_TYPE = {
    "TYPE_INTERNAL_EVENTSOURCE": "eventsource",
}


def _resource_type(params: Dict) -> str:
    """⛔ Juggler says `cause`, Playwright says `resourceType`. The cause is
    the `nsIContentPolicy` constant's NAME, `TYPE_DOCUMENT`; this table used
    to be keyed by `document`, so every request came out `other` - a page
    load, a fetch and an XHR alike. An unmapped cause is still `other`, as
    in the driver, so `request.resource_type` agrees across transports."""
    internal = params.get("internalCause") or ""
    cause = params.get("cause") or ""
    return (_INTERNAL_CAUSE_TO_RESOURCE_TYPE.get(internal)
            or _CAUSE_TO_RESOURCE_TYPE.get(cause)
            or "other")


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
