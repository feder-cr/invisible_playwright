"""HUNT SCAFFOLDING, NEVER MERGED: put time stamps into the Juggler of an engine, to see where a typed key's delay is born.

Usage: python scripts/hunt_patch_omni.py ours|stock ENGINE_EXE [--dry-run]

ENGINE_EXE is the browser executable. The Juggler lives in the omni.ja that sits NEXT TO the executable (the GRE one), not in
browser/omni.ja: ours as `modules/juggler/{protocol/PageHandler.js,content/PageAgent.js}`, stock Playwright's as
`chrome/juggler/content/{protocol/PageHandler.js,content/PageAgent.js}`. A tree that ships it loose (modules/juggler/ on disk) is patched
in place. The first run keeps `omni.ja.orig` (or `<file>.orig`) next to what it changed.

Two shapes of PageAgent._dispatchKeyEvent are known: the one with a latency TIMER (`await new Promise(f => setTimeout(f, latency))`, the
engines before the "key born in the past" fix; stock has no latency at all) and the one WITHOUT a timer (the latency goes to the text
input processor with `tip.setNextKeyLatency(born)` just before the key). The patcher finds whichever is there.

Every stamp is a line

    HUNTKEY <proc> <pid> <call> <tag> <wall_ms> <type> <urlencoded key> [extra]

and it is only KEPT IN MEMORY while keys are typed (an array push, nothing else: no dump(), no page access). It is written once, at the
end: dump() when the process shuts down ('quit-application' / 'xpcom-shutdown' observers) or, in the content process, when the page is
hidden (pagehide); and the content process also hands its lines to the page when the page's script asks for them, with
`window.dispatchEvent(new Event('hunt-flush'))` and then reads `window.__hs` (the listener is installed once per window, after the
first key). A Windows content process may have no stdout, so that is the way that always works.

    P1   parent: Page.dispatchKeyEvent received          (PageHandler.prototype['Page.dispatchKeyEvent'], wrapped)
    C2   content: dispatchKeyEvent received              (PageAgent.prototype._dispatchKeyEvent, wrapped)
    C3   content: just before tip.setNextKeyLatency(born) (no-timer shape; extra latency=<ms>), or the latency timer armed (timer shape;
         extra delay=<ms>)
    C4   content: just after tip.setNextKeyLatency(born) (no-timer shape), or the timer's callback runs (timer shape)
    C4r  content: the await resumed                      (timer shape only)
    C5   content: just before the DOM event is made      (tip.keydown / tip.keyup / tip.commitCompositionWith)
    C6   content: _dispatchKeyEvent returned
    P7   parent: the handler is about to answer

`wall_ms` is the wall clock with sub-millisecond digits: Date.now() read on the tick it changes, once per process, as an anchor for
ChromeUtils.now() (monotonic, microseconds). dump() needs `browser.dom.window.dump.enabled` (off in a release build): the caller passes it.
The stock engine has no latency, so it has P1, C2, C5, C6, P7 only.

SEND-SPLIT hunt additions (the parent's side of the hop "sent -> P1"; S0 = the client decided, S1 = os.write returned in the core's
Connection._write, both stamped by scripts/hunt_typing.py; S3 = P1):

    S2   parent: the pipe's message reached JS (components/Juggler.js, the nsIRemoteDebuggingPipeClient's receiveMessage, the first JS on the
         parent's main thread after the C++ reader thread handed the message over), only for Page.dispatchKeyEvent; extra msg=<urlencoded JSON>
    S2b  parent: Dispatcher._dispatch entered (protocol/Dispatcher.js, before the JSON.parse), the same messages; extra msg=
    D    parent, EVERY inbound message (a ring buffer, 8192 entries, written once at the end): `<wall of entry> - - m=<method> id=<id>
         sync=<ms of the synchronous part, entry to the first await> total=<ms until the answer was sent> t1=<wall at the end of the
         synchronous part> t2=<wall at the end> len=<characters>`; written when the message finished
    E    parent, EVERY event Dispatcher._emitEvent sent to the client (the same ring): `m=<event> sync=<ms> t1=<wall at the end>`
    A0   each patched module of a process says the anchor it calibrated (extra module=<file> anchor=<ms>); a module reuses the one an earlier
         module of the same global left in globalThis.__HKANCHOR, so the stamps of Juggler.js, Dispatcher.js and PageHandler.js share one clock
         when the modules share a global, and the summary shows the spread when they do not.
The three extra files are optional for stock (a Juggler that does not match is reported and left alone) and required for ours.
"""
import json
import os
import re
import shutil
import sys
import zipfile

HELPER = r"""
// ---- HUNTKEY begin: hunt scaffolding, never merged ----
const __HK = (() => {
  const clk = () => { try { return ChromeUtils.now(); } catch (e) { return NaN; } };
  let anchor = NaN;
  let shared = false;
  try {
    if (typeof globalThis.__HKANCHOR === 'number') {
      anchor = globalThis.__HKANCHOR;
      shared = true;
    } else {
      const t0 = clk();
      const a = Date.now();
      let b = a;
      while (b === a && clk() - t0 < 30) b = Date.now();
      anchor = b - clk();
      globalThis.__HKANCHOR = anchor;
    }
  } catch (e) {}
  const wall = () => (anchor === anchor) ? anchor + clk() : Date.now();
  let pid = 0;
  try { pid = Services.appinfo.processID; } catch (e) {}
  const buf = [];
  let seq = 0;
  let dumped = 0;
  // written once: what has not been written yet goes out in ONE dump(); called at process end and when a page is hidden
  const writeOut = () => {
    if (dumped >= buf.length) return;
    const lines = buf.slice(dumped);
    dumped = buf.length;
    try { dump(lines.map(l => 'HUNTKEY ' + l + '\n').join('')); } catch (e) {}
  };
  try {
    const obs = { observe() { writeOut(); } };
    Services.obs.addObserver(obs, 'quit-application');
    Services.obs.addObserver(obs, 'xpcom-shutdown');
  } catch (e) {}
  // the ring of the parent's main-thread activity: written once at the end with the other lines, the oldest entries overwritten
  const RING = 8192;
  const ring = new Array(RING);
  let ringN = 0;
  let ringDumped = 0;
  const writeRing = () => {
    const from = Math.max(ringDumped, ringN - RING);
    if (from >= ringN) return;
    const lines = [];
    for (let i = from; i < ringN; i++) lines.push('HUNTKEY ' + ring[i % RING] + '\n');
    ringDumped = ringN;
    try { dump(lines.join('')); } catch (e) {}
  };
  try {
    const obs2 = { observe() { writeRing(); } };
    Services.obs.addObserver(obs2, 'quit-application');
    Services.obs.addObserver(obs2, 'xpcom-shutdown');
  } catch (e) {}
  const hooked = new WeakSet();
  return {
    now: wall,
    // a message that arrived: only a key event is kept (raw, urlencoded: nothing is parsed here), the rest of the stamp is the clock
    raw(tag, message) {
      try {
        if (typeof message !== 'string' || message.indexOf('Page.dispatchKeyEvent') < 0) return;
        buf.push(['parent', pid, seq, tag, wall().toFixed(3), '-', '-', 'msg=' + encodeURIComponent(message)].join(' '));
      } catch (e) {}
    },
    // one entry of the ring (a finished message or an emitted event)
    ringPush(tag, t0, extra) {
      try {
        ring[ringN++ % RING] = ['parent', pid, 0, tag, t0.toFixed(3), '-', '-', extra].join(' ');
      } catch (e) {}
    },
    module(name) {
      try { buf.push(['parent', pid, 0, 'A0', wall().toFixed(3), '-', '-', 'module=' + name + ' anchor=' + anchor.toFixed(3) + ' shared=' + shared].join(' ')); } catch (e) {}
    },
    stamp(proc, tag, type, key, extra) {
      try {
        if (tag === 'P1' || tag === 'C2') seq++;
        buf.push([proc, pid, seq, tag, wall().toFixed(3), type, encodeURIComponent(String(key)), extra || ''].join(' '));
      } catch (e) {}
    },
    // content process, once per window and after its first key: the page asks for the lines with a 'hunt-flush' event; pagehide writes
    hook(win) {
      try {
        if (!win || hooked.has(win)) return;
        hooked.add(win);
        win.addEventListener('hunt-flush', () => {
          try {
            const w = win.wrappedJSObject;
            w.__hs = new w.Array();
            for (const l of buf) w.__hs.push(l);
          } catch (e) {}
        }, true);
        win.addEventListener('pagehide', writeOut, true);
      } catch (e) {}
    },
    writeOut,
  };
})();
"""

PARENT_WRAP = r"""
__HK.module('PageHandler.js');
(() => {
  const name = 'Page.dispatchKeyEvent';
  const proto = (typeof PageHandler === 'function') ? PageHandler.prototype : null;
  const orig = proto && proto[name];
  if (typeof orig !== 'function') return;
  proto[name] = async function(params) {
    const t = params && params.type, k = params && params.key;
    __HK.stamp('parent', 'P1', t, k, 'latency=' + (params && params.latency));
    try {
      return await orig.call(this, params);
    } finally {
      __HK.stamp('parent', 'P7', t, k);
    }
  };
})();
// ---- HUNTKEY end ----
"""

CONTENT_WRAP = r"""
(() => {
  const proto = (typeof PageAgent === 'function') ? PageAgent.prototype : null;
  const orig = proto && proto._dispatchKeyEvent;
  if (typeof orig !== 'function') return;
  proto._dispatchKeyEvent = async function(params) {
    const t = params && params.type, k = params && params.key;
    __HK.stamp('content', 'C2', t, k, 'latency=' + (params && params.latency));
    try {
      return await orig.call(this, params);
    } finally {
      __HK.stamp('content', 'C6', t, k);
      try { __HK.hook(this._frameTree.mainFrame().domWindow()); } catch (e) {}
    }
  };
})();
// ---- HUNTKEY end ----
"""

# Juggler.js: only the helper (S2 is stamped by the call put into receiveMessage), Dispatcher.js: S2b, the ring of every message and event
JUGGLER_TAIL = r"""
__HK.module('Juggler.js');
// ---- HUNTKEY end ----
"""

DISPATCHER_WRAP = r"""
__HK.module('Dispatcher.js');
(() => {
  const proto = (typeof Dispatcher === 'function') ? Dispatcher.prototype : null;
  if (!proto) return;
  const origDispatch = proto._dispatch;
  const origEmit = proto._emitEvent;
  const reMethod = /"method"\s*:\s*"([^"]*)"/;
  const reId = /"id"\s*:\s*(\d+)/;
  if (typeof origDispatch === 'function') {
    proto._dispatch = function(event) {
      const t0 = __HK.now();
      const data = event && event.data;
      let method = '?', id = '?', len = 0;
      try {
        if (typeof data === 'string') {
          len = data.length;
          const m = reMethod.exec(data), i = reId.exec(data);
          if (m) method = m[1];
          if (i) id = i[1];
          __HK.raw('S2b', data);
        }
      } catch (e) {}
      const p = origDispatch.call(this, event);   // the synchronous part (parse, check, the handler up to its first await) runs here
      const t1 = __HK.now();
      const done = () => {
        const t2 = __HK.now();
        __HK.ringPush('D', t0, 'm=' + method + ' id=' + id + ' sync=' + (t1 - t0).toFixed(3) + ' total=' + (t2 - t0).toFixed(3)
          + ' t1=' + t1.toFixed(3) + ' t2=' + t2.toFixed(3) + ' len=' + len);
      };
      try { p.then(done, done); } catch (e) { done(); }
      return p;
    };
  }
  if (typeof origEmit === 'function') {
    proto._emitEvent = function(sessionId, eventName, params) {
      const t0 = __HK.now();
      try {
        return origEmit.call(this, sessionId, eventName, params);
      } finally {
        const t1 = __HK.now();
        __HK.ringPush('E', t0, 'm=' + eventName + ' sync=' + (t1 - t0).toFixed(3) + ' t1=' + t1.toFixed(3));
      }
    };
  }
})();
// ---- HUNTKEY end ----
"""

MARK = "HUNTKEY begin"

# The latency TIMER of the engines before the "key born in the past" fix (a single statement under `if (latency > 0)`, so it is replaced by a block).
TIMER = re.compile(r"await new Promise\(\s*f\s*=>\s*setTimeout\(\s*f\s*,\s*latency\s*\)\s*\);")
TIMER_NEW = ("{ __HK.stamp('content', 'C3', type, key, 'delay=' + latency); "
             "await new Promise(f => setTimeout(() => { __HK.stamp('content', 'C4', type, key); f(); }, latency)); "
             "__HK.stamp('content', 'C4r', type, key); }")
# The no-timer shape: the latency is declared to the text input processor for the next key, once in each of the three ways in (the
# `tip.setNextKeyLatency(0)` of the finally is left alone). Stamped before and after, as statements of the same block.
BORN = re.compile(r"\btip\.setNextKeyLatency\(born\);")
BORN_NEW = ("__HK.stamp('content', 'C3', type, key, 'latency=' + born); tip.setNextKeyLatency(born); "
            "__HK.stamp('content', 'C4', type, key);")
# The ways the function hands the event to the TextInputProcessor (PageAgent.js:944, 947, 951 in the source tree).
TIP = re.compile(r"\btip\.(keydown|keyup|commitCompositionWith)\(")
FUNC = re.compile(r"async _dispatchKeyEvent\(\{[^}]*\}\)\s*\{")
NEXT_METHOD = re.compile(r"\n  (?:async )?[A-Za-z_]\w*\(")


def patch_page_handler(text):
    anchors = {"class PageHandler": bool(re.search(r"\bclass PageHandler\b", text)),
               "Page.dispatchKeyEvent": "['Page.dispatchKeyEvent']" in text}
    if MARK in text:
        return text, dict(anchors, already=True)
    if not all(anchors.values()):
        return None, anchors
    return text.rstrip("\n") + "\n" + HELPER + PARENT_WRAP, anchors


# the pipe client's receiveMessage in components/Juggler.js (juggler/components/Juggler.js:123 in the source tree)
RECV = re.compile(r"(receiveMessage\(message\)\s*\{)(\s*if \(this\.onmessage\))")


def patch_juggler(text):
    n = len(re.findall(r"receiveMessage\(message\)", text))
    anchors = {"pipe client receiveMessage(message)": n,
               "nsIRemoteDebuggingPipeClient": "nsIRemoteDebuggingPipeClient" in text}
    if MARK in text:
        return text, dict(anchors, already=True)
    if n != 1 or not anchors["nsIRemoteDebuggingPipeClient"]:
        return None, anchors
    new, k = RECV.subn(lambda m: m.group(1) + " __HK.raw('S2', message);" + m.group(2), text)
    anchors["receiveMessage call patched"] = k
    if k != 1:
        return None, anchors
    return new.rstrip("\n") + "\n" + HELPER + JUGGLER_TAIL, anchors


def patch_dispatcher(text):
    anchors = {"class Dispatcher": bool(re.search(r"class Dispatcher", text)),
               "async _dispatch(event)": bool(re.search(r"async _dispatch\(event\)", text)),
               "JSON.parse(event.data)": "JSON.parse(event.data)" in text,
               "_emitEvent": bool(re.search(r"_emitEvent\(", text))}
    if MARK in text:
        return text, dict(anchors, already=True)
    if not all(anchors.values()):
        return None, anchors
    return text.rstrip("\n") + "\n" + HELPER + DISPATCHER_WRAP, anchors


def patch_page_agent(text):
    anchors = {"class PageAgent": bool(re.search(r"\bclass PageAgent\b", text))}
    m = FUNC.search(text)
    anchors["_dispatchKeyEvent"] = bool(m)
    if MARK in text:
        return text, dict(anchors, already=True)
    if not (anchors["class PageAgent"] and m):
        return None, anchors
    n = NEXT_METHOD.search(text, m.end())
    start, end = m.start(), (n.start() if n else len(text))
    body = text[start:end]
    body, n_timer = TIMER.subn(lambda _m: TIMER_NEW, body)
    body, n_born = BORN.subn(lambda _m: BORN_NEW, body)
    body, n_tip = TIP.subn(
        lambda t: "(__HK.stamp('content', 'C5', type, key, '%s'), tip).%s(" % (t.group(1), t.group(1)), body)
    # the timer (old engines) and setNextKeyLatency (new ones) are alternatives and stock has neither: not an error, only reported
    anchors["latency timer"] = n_timer == 1
    anchors["setNextKeyLatency(born)"] = n_born
    anchors["tip calls"] = n_tip
    if n_timer and n_born:
        return None, dict(anchors, error="both a timer and setNextKeyLatency(born) in one function")
    if n_tip < 1:
        return None, anchors
    if KIND == "ours" and not (n_born == 3 or n_timer == 1):
        return None, dict(anchors, error="our engine has neither the latency timer nor three setNextKeyLatency(born) calls")
    return text[:start] + body + text[end:] + "\n" + HELPER + CONTENT_WRAP, anchors


KIND = "ours"  # set by main(): ours must have one of the two shapes of the latency, stock has neither
JOBS = {"PageHandler.js": patch_page_handler, "PageAgent.js": patch_page_agent, "Juggler.js": patch_juggler, "Dispatcher.js": patch_dispatcher}
# the files of the send-split hunt: required for ours, for stock only when its file matches
OPTIONAL_FOR_STOCK = {"Juggler.js", "Dispatcher.js"}


def rewrite_jar(path, replacements):
    """Write `path` again with `replacements` (name -> bytes): the same members in the same order, stored ones stored and deflated ones
    deflated, as a plain zip (Firefox reads both that and Mozilla's optimized layout; the core's open_jar reads both too)."""
    from invisible_core.jar import open_jar
    jar = open_jar(path)
    tmp = path + ".hunt.tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        for name, (method, _c, _u, _l) in jar._entries.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if method == 0 else zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, replacements[name] if name in replacements else jar.read(name), compresslevel=6)
    return tmp


def verify_jar(old, new, changed):
    """Every member of the new jar is the old one's, byte for byte, except `changed`; same names."""
    from invisible_core.jar import open_jar
    a, b = open_jar(old), open_jar(new)
    assert a.names() == b.names(), "member names differ: %s" % sorted(a.names() ^ b.names())[:5]
    for name in sorted(a.names()):
        same = a.read(name) == b.read(name)
        if name in changed:
            assert not same, "%s was not changed" % name
        else:
            assert same, "%s changed" % name


def patch_omni(path, dry_run):
    from invisible_core.jar import open_jar
    jar = open_jar(path)
    found = {}
    for name in sorted(jar.names()):
        base = name.rsplit("/", 1)[-1]
        if base in JOBS and "juggler" in name:
            found[name] = base
    if not found:
        return None
    report = {"omni": path, "members": {}}
    replacements = {}
    for name, base in found.items():
        new, anchors = JOBS[base](jar.read(name).decode("utf-8"))
        report["members"][name] = anchors
        if new is None and KIND == "stock" and base in OPTIONAL_FOR_STOCK:
            report["members"][name] = dict(anchors, skipped="no match in stock, left alone")
            print("SKIPPED %s (stock): %s" % (name, json.dumps(anchors)))
            continue
        if new is None:
            raise SystemExit("ANCHOR MISSING in %s %s: %s" % (path, name, json.dumps(anchors)))
        if not anchors.get("already"):
            replacements[name] = new.encode("utf-8")
    report["changed"] = sorted(replacements)
    if replacements and not dry_run:
        tmp = rewrite_jar(path, replacements)
        verify_jar(path, tmp, set(replacements))
        if not os.path.exists(path + ".orig"):
            shutil.copy2(path, path + ".orig")
        os.replace(tmp, path)
        report["written"] = os.path.getsize(path)
    return report


def patch_loose(root, dry_run):
    report = {"loose": root, "members": {}, "changed": []}
    for dirpath, _dirs, files in os.walk(os.path.join(root, "modules", "juggler")):
        for f in files:
            if f in JOBS:
                p = os.path.join(dirpath, f)
                with open(p, encoding="utf-8") as fh:
                    new, anchors = JOBS[f](fh.read())
                report["members"][p] = anchors
                if new is None and KIND == "stock" and f in OPTIONAL_FOR_STOCK:
                    report["members"][p] = dict(anchors, skipped="no match in stock, left alone")
                    print("SKIPPED %s (stock): %s" % (p, json.dumps(anchors)))
                    continue
                if new is None:
                    raise SystemExit("ANCHOR MISSING in %s: %s" % (p, json.dumps(anchors)))
                if not anchors.get("already"):
                    report["changed"].append(p)
                    if not dry_run:
                        if not os.path.exists(p + ".orig"):
                            shutil.copy2(p, p + ".orig")
                        with open(p, "w", encoding="utf-8", newline="\n") as fh:
                            fh.write(new)
    return report if report["members"] else None


def main():
    global KIND
    kind, exe = sys.argv[1], sys.argv[2]
    KIND = kind
    dry = "--dry-run" in sys.argv
    root = os.path.dirname(os.path.abspath(exe))
    reports = []
    # the GRE omni.ja next to the executable first; browser/omni.ja only to say it was looked at
    for rel in ("omni.ja", os.path.join("browser", "omni.ja")):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            r = patch_omni(p, dry)
            print("looked in %s: %s" % (p, "juggler found" if r else "no juggler"))
            if r:
                reports.append(r)
    if not reports:
        r = patch_loose(root, dry)
        print("looked at the loose tree %s: %s" % (root, "juggler found" if r else "no juggler"))
        if r:
            reports.append(r)
    if not reports:
        raise SystemExit("no Juggler PageHandler.js / PageAgent.js in any omni.ja or loose tree under %s" % root)
    for r in reports:
        print(json.dumps(r, indent=1))
        members = r["members"]
        got = {k.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for k in members}
        need = {"PageHandler.js", "PageAgent.js"} | (set() if kind == "stock" else OPTIONAL_FOR_STOCK)
        if not need <= got:
            raise SystemExit("expected %s, found %s" % (sorted(need), sorted(members)))
        skipped = sorted(k for k, a in members.items() if a.get("skipped"))
        print("send-split points %s" % ("stock: skipped " + ", ".join(skipped) if skipped else "all patched"))
    if kind == "ours":
        # the engine must still be the one the seal describes (the juggler stays in it with its stealth markers)
        from invisible_core.seal import JUGGLER_MIN_MARKED, read_engine_identity
        ident = read_engine_identity(exe)
        print("engine identity after the patch: juggler_present=%s marked=%s layout=%s" % (
            ident.juggler_present, ident.marked_entries, ident.juggler_layout))
        if not dry:
            assert ident.juggler_present and ident.marked_entries >= JUGGLER_MIN_MARKED, "the patched engine lost its markers"
    print("PATCHED" if not dry else "DRY RUN")


if __name__ == "__main__":
    main()
