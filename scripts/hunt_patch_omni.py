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
  try {
    const t0 = clk();
    const a = Date.now();
    let b = a;
    while (b === a && clk() - t0 < 30) b = Date.now();
    anchor = b - clk();
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
  const hooked = new WeakSet();
  return {
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
JOBS = {"PageHandler.js": patch_page_handler, "PageAgent.js": patch_page_agent}


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
        if len(members) != 2:
            raise SystemExit("expected PageHandler.js and PageAgent.js, found %s" % sorted(members))
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
