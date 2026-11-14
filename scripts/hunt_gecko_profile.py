"""HUNT SCAFFOLDING, NEVER MERGED: a reader for the profile the Gecko profiler writes at shutdown (MOZ_PROFILER_SHUTDOWN), only what the hunt needs.

Usage: python scripts/hunt_gecko_profile.py --probe FILE        (what is in the file: format, threads, samples, interval, libs)
       python scripts/hunt_gecko_profile.py --selftest           (the reader against two synthetic profiles in the two layouts it knows)

The formats it reads (the profiler changed its JSON over the years, the candidate's version is one of them and is only known when a file exists):
  tables as {"schema": {col: i}, "data": [[...]]} (the Gecko layout) or as columns {"col": [...], "length": n} (the processed layout);
  strings in profile["shared"]["stringArray"] (new) or thread["stringTable"] / thread["stringArray"] (old);
  frame names in frameTable.location (a string: "fn (url:line:col)" for JS, a label for C++ label frames, "0x<addr>" for an unresolved native frame)
  or frameTable.func -> funcTable.name / fileName / lineNumber (processed layout);
  samples.time in ms since meta.startTime (or samples.timeDeltas, summed), a file that may be gzip.
A native frame without symbols is an address: it is named "<lib>+0x<offset> (unsymbolicated)" from profile["libs"] when the address falls in a library.
"""
import gzip
import json
import re
import sys

IDLE_NAME = re.compile(r"(WaitFor|NtWait|ZwWait|GetMessage|MsgWait|ThreadEventQueue::GetEvent|PR_WaitCondVar|PR_Wait|nsThread::ProcessNextEvent.*wait)", re.I)
JS_LOC = re.compile(r"^(?P<fn>.*?)\s*\((?P<url>[^()]*?)(?::(?P<line>\d+))?(?::(?P<col>\d+))?\)$")
JS_BARE = re.compile(r"^(?P<url>[^\s()]+\.(?:m?js|jsm|sys\.mjs|html|xhtml)(?:\?[^\s:]*)?)(?::(?P<line>\d+))?(?::(?P<col>\d+))?$")


def load(path):
    with open(path, "rb") as f:
        head = f.read(2)
    opener = gzip.open if head == b"\x1f\x8b" else open
    with opener(path, "rb") as f:
        return json.loads(f.read().decode("utf-8", "replace"))


def cols(table):
    """a table in either layout -> {column: list}"""
    if not isinstance(table, dict):
        return {}
    if "schema" in table and "data" in table:
        schema, data = table["schema"], table["data"]
        return {name: [row[i] if i < len(row) else None for row in data] for name, i in schema.items()}
    return {k: v for k, v in table.items() if isinstance(v, list)}


def basename(url):
    return url.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].split("?")[0]


class Thread:
    """One thread of a profile with its stacks resolved to function names."""

    def __init__(self, profile, th):
        self.raw = th
        self.name = th.get("name", "?")
        self.process_type = th.get("processType") or th.get("processName") or "?"
        self.pid = th.get("pid")
        shared = (profile.get("shared") or {}).get("stringArray")
        strs = shared or th.get("stringArray") or th.get("stringTable") or []
        self.strs = strs
        self.libs = profile.get("libs") or []
        meta = profile.get("meta") or {}
        self.categories = [c.get("name") if isinstance(c, dict) else c for c in meta.get("categories") or []]
        self.start_time = meta.get("startTime")
        self.interval = meta.get("interval")
        s = cols(th.get("samples"))
        self.s_stack = s.get("stack") or []
        if s.get("time"):
            self.s_time = [t for t in s["time"]]
        elif s.get("timeDeltas"):
            acc, out = 0.0, []
            for d in s["timeDeltas"]:
                acc += d or 0.0
                out.append(acc)
            self.s_time = out
        else:
            self.s_time = []
        st = cols(th.get("stackTable"))
        self.st_prefix, self.st_frame = st.get("prefix") or [], st.get("frame") or []
        fr = cols(th.get("frameTable"))
        self.fr_loc = fr.get("location")
        self.fr_func = fr.get("func")
        self.fr_cat = fr.get("category") or []
        self.fr_line = fr.get("line") or []
        ft = cols(th.get("funcTable")) if self.fr_func is not None else {}
        self.fn_name, self.fn_file, self.fn_line = ft.get("name") or [], ft.get("fileName") or [], ft.get("lineNumber") or []
        self._frame = {}
        self._chain = {}

    def string(self, i):
        if isinstance(i, str):
            return i
        return self.strs[i] if isinstance(i, int) and 0 <= i < len(self.strs) else ""

    def frame_info(self, fi):
        """-> (key, kind, name, file, category name): kind 'js' | 'label' | 'native'"""
        got = self._frame.get(fi)
        if got:
            return got
        loc, file = "", ""
        if self.fr_loc is not None:
            loc = self.string(self.fr_loc[fi])
        elif self.fr_func is not None:
            f = self.fr_func[fi]
            loc = self.string(self.fn_name[f]) if f is not None and f < len(self.fn_name) else ""
            file = self.string(self.fn_file[f]) if f is not None and f < len(self.fn_file) and self.fn_file[f] is not None else ""
        cat = ""
        if fi < len(self.fr_cat) and isinstance(self.fr_cat[fi], int) and self.fr_cat[fi] < len(self.categories):
            cat = self.categories[self.fr_cat[fi]] or ""
        if file:
            kind, name, base = "js", loc or "(anonymous)", basename(file)
            key = "%s @ %s" % (name, base)
        elif re.match(r"^0x[0-9a-fA-F]+$", loc):
            kind, name, base = "native", self._native(int(loc, 16)), ""
            key = name
        else:
            m = JS_LOC.match(loc)
            b = JS_BARE.match(loc)
            if m and re.search(r"(:|\.m?js|\.jsm|\.html)", m.group("url") or ""):
                kind, name, base = "js", m.group("fn").strip() or "(anonymous)", basename(m.group("url"))
                key = "%s @ %s" % (name, base)
            elif b:
                kind, name, base = "js", "(anonymous)", basename(b.group("url"))
                key = "%s @ %s" % (name, base)
            else:
                kind, name, base = "label", loc or "(empty)", ""
                key = name
        got = (key, kind, name, base, cat)
        self._frame[fi] = got
        return got

    def _native(self, addr):
        for lib in self.libs:
            try:
                start, end = int(lib.get("start", 0)), int(lib.get("end", 0))
            except (TypeError, ValueError):
                continue
            if start <= addr < end:
                return "%s+0x%x (unsymbolicated)" % (lib.get("name") or lib.get("debugName") or "?", addr - start + int(lib.get("offset", 0)))
        return "0x%x (unsymbolicated, no library)" % addr

    def chain(self, stack, depth=60):
        """frame indices leaf -> root of a stack"""
        c = self._chain.get(stack)
        if c is not None:
            return c
        out, s, n = [], stack, 0
        while s is not None and n < depth:
            out.append(self.st_frame[s])
            s = self.st_prefix[s] if s < len(self.st_prefix) else None
            n += 1
        self._chain[stack] = out
        return out

    def is_idle(self, chain):
        for fi in chain[:6]:
            k, kind, name, base, cat = self.frame_info(fi)
            if cat == "Idle" or (kind != "js" and IDLE_NAME.search(name)):
                return True
        return False

    def samples_between(self, a, b):
        """[(time, stack)] with a <= time <= b (profile ms); samples are in time order"""
        from bisect import bisect_left, bisect_right
        lo, hi = bisect_left(self.s_time, a), bisect_right(self.s_time, b)
        return [(self.s_time[i], self.s_stack[i]) for i in range(lo, hi) if self.s_stack[i] is not None]

    def functions_of_all_samples(self):
        """the set of function keys of every stack any sample refers to (the whole life of the thread)"""
        keys = set()
        for st in set(x for x in self.s_stack if x is not None):
            for fi in self.chain(st):
                keys.add(self.frame_info(fi)[0])
        return keys


def parent_main_thread(profile):
    """the Thread of the parent process's main thread (GeckoMain, processType default), None if the file has none"""
    best = None
    for th in profile.get("threads") or []:
        if th.get("name") != "GeckoMain":
            continue
        t = Thread(profile, th)
        if (th.get("processType") in ("default", None, "parent")) or "Parent" in str(th.get("processName", "")):
            if best is None or len(t.s_time) > len(best.s_time):
                best = t
    if best is None:
        for th in profile.get("threads") or []:
            if th.get("name") == "GeckoMain":
                return Thread(profile, th)
    return best


def probe(path):
    p = load(path)
    meta = p.get("meta") or {}
    print("file %s" % path)
    print("top-level keys: %s" % sorted(p))
    print("meta.version %s, preprocessedProfileVersion %s, product %s, interval %s, startTime %s, abi %s, shutdownTime %s" % (
        meta.get("version"), meta.get("preprocessedProfileVersion"), meta.get("product"), meta.get("interval"),
        meta.get("startTime"), meta.get("abi"), meta.get("shutdownTime")))
    print("string layout: %s" % ("shared.stringArray" if (p.get("shared") or {}).get("stringArray") else "per-thread"))
    print("libs: %d (first: %s)" % (len(p.get("libs") or []), [(l.get("name"), l.get("start")) for l in (p.get("libs") or [])[:3]]))
    print("subprocess profiles inside: %d" % len(p.get("processes") or []))
    for th in p.get("threads") or []:
        t = Thread(p, th)
        iv = sorted(b - a for a, b in zip(t.s_time, t.s_time[1:]))
        print("thread %-12s processType %-8s pid %s: %d samples, %d stacks, %d frames, time %s .. %s ms, gap p50 %s ms" % (
            t.name, t.process_type, t.pid, len(t.s_time), len(t.st_frame), len(t.fr_loc if t.fr_loc is not None else t.fr_func or []),
            t.s_time[0] if t.s_time else None, t.s_time[-1] if t.s_time else None, round(iv[len(iv) // 2], 3) if iv else None))
    t = parent_main_thread(p)
    if t is None:
        print("NO parent main thread (GeckoMain) in this file")
        return 1
    kinds = {}
    for st in set(x for x in t.s_stack if x is not None):
        for fi in t.chain(st):
            k = t.frame_info(fi)[1]
            kinds[k] = kinds.get(k, 0) + 1
    print("parent main thread frames by kind (over distinct stacks): %s" % kinds)
    top = {}
    for st in t.s_stack:
        if st is not None:
            ch = t.chain(st)
            if ch:
                k = t.frame_info(ch[0])[0]
                top[k] = top.get(k, 0) + 1
    print("leaf functions, top 8: %s" % sorted(top.items(), key=lambda kv: -kv[1])[:8])
    js = sorted({t.frame_info(fi)[0] for st in set(x for x in t.s_stack if x is not None) for fi in t.chain(st) if t.frame_info(fi)[1] == "js"})
    print("JS functions seen: %d, examples: %s" % (len(js), js[:6]))
    return 0


def _synthetic(layout):
    """two tiny profiles, the same content: main thread of the parent, stacks root<-GeckoMain<-onmessage (JS)<-leaf, one idle"""
    strs = ["(root)", "Juggler.js", "receiveMessage (resource:///modules/juggler/components/Juggler.js:123:4)", "0x7ff600001010",
            "mozilla::ipc::MessagePump::Run", "WaitForMultipleObjectsEx"]
    frames = [[0, True, None, None, None, None, 0, 0], [2, True, None, None, None, None, 1, 0], [3, False, None, None, None, None, 2, 0],
              [4, False, None, None, None, None, 2, 0], [5, False, None, None, None, None, 3, 0]]
    stacks = [[None, 0], [0, 3], [1, 1], [2, 2], [1, 4]]
    samples = [[2, 10.0], [3, 11.0], [2, 12.0], [4, 13.0], [4, 14.0]]  # stacks: 2 = JS leaf, 3 = native leaf, 4 = idle leaf
    th_old = {"name": "GeckoMain", "processType": "default", "pid": 4242, "stringTable": strs,
              "samples": {"schema": {"stack": 0, "time": 1}, "data": samples},
              "stackTable": {"schema": {"prefix": 0, "frame": 1}, "data": stacks},
              "frameTable": {"schema": {"location": 0, "relevantForJS": 1, "innerWindowID": 2, "implementation": 3, "line": 4, "column": 5,
                                        "category": 6, "subcategory": 7}, "data": frames}}
    meta = {"version": 30, "interval": 1, "startTime": 1.7e12, "categories": [{"name": "Other"}, {"name": "JavaScript"}, {"name": "DOM"}, {"name": "Idle"}]}
    libs = [{"name": "xul.dll", "start": 0x7ff600000000, "end": 0x7ff700000000, "offset": 0}]
    old = {"meta": meta, "libs": libs, "threads": [th_old]}
    th_new = {"name": "GeckoMain", "processType": "default", "pid": 4242,
              "samples": {"length": 5, "stack": [x[0] for x in samples], "timeDeltas": [10.0, 1.0, 1.0, 1.0, 1.0]},
              "stackTable": {"length": 5, "prefix": [x[0] for x in stacks], "frame": [x[1] for x in stacks]},
              "frameTable": {"length": 5, "location": [f[0] for f in frames], "category": [f[6] for f in frames]}}
    new = {"meta": meta, "libs": libs, "shared": {"stringArray": strs}, "threads": [th_new]}
    return old, new


def selftest():
    old, new = _synthetic(None)
    for label, prof in (("gecko schema layout", old), ("column layout", new)):
        t = parent_main_thread(prof)
        assert t is not None and t.name == "GeckoMain", label
        assert t.s_time == [10.0, 11.0, 12.0, 13.0, 14.0], (label, t.s_time)
        got = t.samples_between(11.0, 13.0)
        assert [s for _t, s in got] == [3, 2, 4], (label, got)
        assert t.frame_info(t.chain(1)[0])[1] == "label"
        leaf = t.frame_info(t.chain(2)[0])
        assert leaf[0] == "receiveMessage @ Juggler.js" and leaf[1] == "js", (label, leaf)
        nat = t.frame_info(t.chain(3)[0])
        assert nat[1] == "native" and nat[0].startswith("xul.dll+0x1010"), (label, nat)
        lab = t.frame_info(t.chain(4)[0])
        assert lab[1] == "label" and lab[0] == "WaitForMultipleObjectsEx", (label, lab)
        assert t.is_idle(t.chain(4)) and not t.is_idle(t.chain(2)), label
        assert "receiveMessage @ Juggler.js" in t.functions_of_all_samples(), label
    print("selftest ok")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    if len(sys.argv) > 2 and sys.argv[1] == "--probe":
        sys.exit(probe(sys.argv[2]))
    print(__doc__)
    sys.exit(2)
