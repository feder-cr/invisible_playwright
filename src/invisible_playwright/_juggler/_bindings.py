"""`expose_binding` / `expose_function`: the script a page runs, and the way a call is read and answered.

THE RULE: THE PAGE SEES `foo` AND NOTHING ELSE. A page can list the names on its window, so a transport left there ("a binding
function", a controller) is the driver's signature. Nothing is left:

* One engine binding PER EXPOSED NAME (`Browser.addBinding` for a context, `Page.addBinding` for a page), under a native name chosen
  for the session. The engine puts the native function on the window of every document of the page or context, present and future,
  in every process, BEFORE any script of the page, and then runs the script below.
* The script takes the native function off the window at once and keeps it in a closure; defines `foo` (an ordinary function whose
  source says nothing of what it is for); and hands the engine, through the native function, the function that answers a call
  (the engine keeps it as an object of that context and reports its id: `payload.bindingEnd`).
* A call travels as a JSON string through the native function; the answer is `Runtime.callFunction` on the kept function.

LIMIT, SAID HERE. Arguments and results cross as JSON: `undefined`, functions and `NaN` arrive as `null`, and a `Date` as its ISO
string. A caller who needs one passes it as a string. Workers do not get the function (the engine installs bindings in the frames
of a page, as Playwright's does).
"""
from __future__ import annotations

import functools
import json


def install_script(native: str, name: str) -> str:
    """Run by the engine in each document, after it exports the native function `native` on the window."""
    return (
        "(() => {"
        " const send = globalThis[%(native)s];"
        " try { delete globalThis[%(native)s]; } catch (e) {}"
        " if (typeof send !== 'function') return;"
        " const text = JSON.stringify, waiting = new Map();"
        " let last = 0;"
        " const ask = (name, args) => new Promise((resolve, reject) => {"
        "   const id = ++last; waiting.set(id, [resolve, reject]);"
        "   try { send(text({ name, seq: id, args })); } catch (e) { waiting.delete(id); reject(e); } });"
        " const f = { [%(name)s]: function (...args) { return ask(%(name)s, args); } }[%(name)s];"
        " Object.defineProperty(globalThis, %(name)s,"
        "   { value: f, writable: true, configurable: true, enumerable: true });"
        " send(function (id, ok, value) {"
        "   const w = waiting.get(id); if (!w) return; waiting.delete(id);"
        "   if (ok) w[0](value); else w[1](new Error(value)); });"
        " })()") % {"native": json.dumps(native), "name": json.dumps(name)}


def remove_script(native: str, name: str) -> str:
    """The same binding again, as the engine runs the last one it was given for a native name: it takes the native function
    off the window and the exposed function with it. A document that exists loses `foo`; one to come never gets it."""
    return ("(() => { try { delete globalThis[%s]; } catch (e) {}"
            " try { delete globalThis[%s]; } catch (e) {} })()") % (json.dumps(native), json.dumps(name))


#: `Runtime.callFunction` with the kept function as the first argument.
DELIVER = "(end, seq, ok, value) => end(seq, ok, value)"


def read_call(payload) -> tuple:
    """`(name, seq, args)` of a `Page.bindingCalled` payload, or `None` if it is not a call."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        return str(data["name"]), int(data["seq"]), list(data.get("args") or [])
    except (TypeError, ValueError, KeyError):
        return None


@functools.lru_cache(maxsize=16)
def engine_supports(engine_dir: str) -> bool:
    """Whether the engine in `engine_dir` has `Page.addBinding` and the binding that hands its end over.

    Read from the engine's own files (the schema of its protocol, and its Runtime), so an engine without them is known BEFORE a
    function is exposed: there is no fallback to a name on the window, and a clear refusal is the answer. An engine that cannot
    be read is treated as one that cannot."""
    if not engine_dir:
        return False
    try:
        import os

        from invisible_core.juggler._gen_protocol import protocol_source
        _label, text = protocol_source(engine_dir)
        if text.count("'addBinding'") < 2:
            return False
        omni = os.path.join(engine_dir, "omni.ja")
        if os.path.isfile(omni):
            from invisible_core.jar import open_jar
            jar = open_jar(omni)
            return b"bindingEnd" in jar.read("modules/juggler/content/Runtime.js")
        return True
    except BaseException:
        return False
