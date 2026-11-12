"""`expose_binding` / `expose_function`: the scripts the page runs, and the way a call is read and answered.

THE SHAPE. One transport per context, one small function per exposed name.

* The transport is Juggler's own binding (`Browser.addBinding`), installed once per context under the name Playwright uses
  (`__playwright__binding__`). The engine exports a native function by that name into the page's main world of every
  document, present and future, in every process, and raises `Page.bindingCalled` when it is called. Its script is the
  controller below: it keeps the calls waiting for an answer and hands each one a promise.
* An exposed name is only a function that asks the controller. It is installed as an init script (the documents to come)
  and run once in the main world of the documents that exist (`defineProperty` on a configurable property, so running it
  twice, as a document born between the two steps would, changes nothing).
* The answer travels the other way: the client runs the Python function, sends `resolve` or `reject` to the BindingCall,
  and the server calls `controller.deliver(seq, ok, value)` in the context the call came from.

LIMIT, SAID HERE. Arguments and results cross as JSON: `undefined`, functions and `NaN` arrive as `null`, and a `Date` as
its ISO string. Playwright's own channel carries those; a caller who needs one passes it as a string.
"""
from __future__ import annotations

import json

#: The native function Juggler exports; Playwright's own name for its transport, so a page that lists its globals sees what
#: a stock Playwright page with an exposed function shows.
TRANSPORT = "__playwright__binding__"
CONTROLLER = "__playwright__binding__controller__"

#: Run by the engine in each main world right after it exports the native function. Everything the page's own code could
#: replace later (`JSON.stringify`, `Map`, `Promise`) is taken now, before any of it runs.
CONTROLLER_JS = """(() => {
  if (globalThis.%(controller)s) return;
  const native = globalThis.%(transport)s;
  const stringify = JSON.stringify, pending = new Map();
  let seq = 0;
  Object.defineProperty(globalThis, '%(controller)s', {
    value: Object.freeze({
      call(name, args) {
        return new Promise((resolve, reject) => {
          const id = ++seq;
          pending.set(id, { resolve, reject });
          try { native(stringify({ name, seq: id, args })); }
          catch (e) { pending.delete(id); reject(e); }
        });
      },
      deliver(id, ok, value) {
        const waiting = pending.get(id);
        if (!waiting) return;
        pending.delete(id);
        if (ok) waiting.resolve(value); else waiting.reject(new Error(value));
      },
    }),
    enumerable: false, configurable: true, writable: false,
  });
})()""" % {"controller": CONTROLLER, "transport": TRANSPORT}


def install_script(name: str) -> str:
    """The function `name` on the page: it asks the controller, which is looked up at the CALL (the init script that
    makes this function may run before the engine has installed the controller)."""
    return ("(() => { const name = %s;"
            " const f = function (...args) { return globalThis.%s.call(name, args); };"
            " Object.defineProperty(f, 'name', { value: name, configurable: true });"
            " Object.defineProperty(globalThis, name, { value: f, writable: true, configurable: true, enumerable: true });"
            " })()") % (json.dumps(name), CONTROLLER)


def remove_script(name: str) -> str:
    return "(() => { try { delete globalThis[%s]; } catch (e) {} })()" % json.dumps(name)


#: In the main world of the call's context: answer the call `seq`.
DELIVER = ("(seq, ok, value) => { const c = globalThis.%s;"
           " if (c) c.deliver(seq, ok, value); }") % CONTROLLER


def read_call(payload) -> tuple:
    """`(name, seq, args)` of a `Page.bindingCalled` payload, or `None` if it is not one of ours."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        return str(data["name"]), int(data["seq"]), list(data.get("args") or [])
    except (TypeError, ValueError, KeyError):
        return None
