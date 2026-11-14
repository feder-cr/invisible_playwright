"""HUNT SCAFFOLDING, NEVER MERGED: a reader for the profile the Gecko profiler writes at shutdown (MOZ_PROFILER_SHUTDOWN), only what the hunt needs.

Usage: python scripts/hunt_gecko_profile.py --probe FILE [--symbols DIR]   (what is in the file: format, threads, samples, interval, libs; with --symbols the
                                                                          native leaf frames of the parent main thread are named from the Breakpad symbols)
       python scripts/hunt_gecko_profile.py --selftest           (the reader against two synthetic profiles in the two layouts it knows)

The formats it reads (the profiler changed its JSON over the years, the candidate's version is one of them and is only known when a file exists):
  tables as {"schema": {col: i}, "data": [[...]]} (the Gecko layout) or as columns {"col": [...], "length": n} (the processed layout);
  strings in profile["shared"]["stringArray"] (new) or thread["stringTable"] / thread["stringArray"] (old);
  frame names in frameTable.location (a string: "fn (url:line:col)" for JS, a label for C++ label frames, "0x<addr>" for an unresolved native frame)
  or frameTable.func -> funcTable.name / fileName / lineNumber (processed layout);
  samples.time in ms since meta.startTime (or samples.timeDeltas, summed), a file that may be gzip.
A native frame without symbols is an address: it is named "<lib>+0x<offset> (unsymbolicated)" from profile["libs"] when the address falls in a library.

Symbols (class Symbols): the candidate's Breakpad symbols (artifact symbols-win-x86_64: zips of `<debug name>/<debug id>/<name>.sym`, made by ./mach buildsymbols).
`Thread.symbolizer = Symbols(...)` makes the native frames of every Thread created afterwards resolve to function names: the module is found by the lib's
debugName and breakpadId (the debug id of the shipped binary), the offset is address - lib start (an RVA, as in the .sym), and the name comes from the FUNC record
that holds it, else the nearest PUBLIC record below it. A resolved frame is keyed "<name without parameters> [native:<lib>]". Memory is bounded: a module is
extracted and indexed only when a frame of it is asked for (addresses, sizes and file offsets of FUNC / PUBLIC records in arrays; the names are read back from
the file on demand), never the whole zip.
"""
import bisect
import os
import shutil
import tempfile
import zipfile
from array import array
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


def short_name(n):
    """a demangled .sym name without its parameter list and cv-qualifiers: 'a::B::c(int, char) const' -> 'a::B::c'"""
    n = n.strip()
    for q in (" const", " volatile"):
        if n.endswith(q):
            n = n[:-len(q)]
    if n.endswith(")"):
        depth = 0
        for i in range(len(n) - 1, -1, -1):
            if n[i] == ")":
                depth += 1
            elif n[i] == "(":
                depth -= 1
                if depth == 0:
                    return n[:i].strip() or n
    return n


class SymModule:
    """One .sym file: FUNC and PUBLIC records indexed by address; names read back from the file when asked."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        faddr, fsize, foff = array("Q"), array("I"), array("Q")
        paddr, poff = array("Q"), array("Q")
        off = 0
        for line in self.f:
            n = len(line)
            c = line[:1]
            try:
                if c == b"F" and line.startswith(b"FUNC "):
                    rest = line[5:]
                    if rest[:2] == b"m ":
                        rest = rest[2:]
                    parts = rest.split(b" ", 3)
                    faddr.append(int(parts[0], 16))
                    fsize.append(min(int(parts[1], 16), 0xFFFFFFFF))
                    foff.append(off)
                elif c == b"P" and line.startswith(b"PUBLIC "):
                    rest = line[7:]
                    if rest[:2] == b"m ":
                        rest = rest[2:]
                    parts = rest.split(b" ", 2)
                    paddr.append(int(parts[0], 16))
                    poff.append(off)
            except (ValueError, IndexError):
                pass
            off += n
        order = sorted(range(len(faddr)), key=faddr.__getitem__)
        self.faddr = array("Q", (faddr[i] for i in order))
        self.fsize = array("I", (fsize[i] for i in order))
        self.foff = array("Q", (foff[i] for i in order))
        order = sorted(range(len(paddr)), key=paddr.__getitem__)
        self.paddr = array("Q", (paddr[i] for i in order))
        self.poff = array("Q", (poff[i] for i in order))
        self.names = {}

    def _name(self, off, public):
        got = self.names.get(off)
        if got is None:
            self.f.seek(off)
            line = self.f.readline().decode("utf-8", "replace").rstrip("\r\n")
            rest = line.split(" ", 1)[1] if " " in line else line
            if rest.startswith("m "):
                rest = rest[2:]
            parts = rest.split(" ", 2 if public else 3)
            got = short_name(parts[-1] if len(parts) > 2 else "")
            self.names[off] = got
        return got

    def lookup(self, rva):
        i = bisect.bisect_right(self.faddr, rva) - 1
        if i >= 0 and rva < self.faddr[i] + self.fsize[i]:
            return self._name(self.foff[i], False)
        j = bisect.bisect_right(self.paddr, rva) - 1
        if j >= 0 and rva - self.paddr[j] < 0x10000:
            return self._name(self.poff[j], True)
        return None

    def close(self):
        try:
            self.f.close()
        except OSError:
            pass


class Symbols:
    """Breakpad symbols from zips and / or directories: `Symbols(path, ...)`; resolve(lib, rva) -> function name or None."""

    def __init__(self, *roots):
        self.index = {}    # (debug name lower, debug id upper) -> (zipfile or None, member name or path)
        self.by_name = {}  # debug name lower -> [ids]
        self.mods = {}
        self.tmp = None
        self.zips = []
        self.stats = {"resolved": 0, "no record at the address": 0, "module not in the symbols": {}, "modules parsed": 0}
        for r in roots:
            if not r:
                continue
            if os.path.isdir(r):
                for dp, _dn, fns in os.walk(r):
                    for fn in fns:
                        full = os.path.join(dp, fn)
                        if fn.endswith(".zip"):
                            self._add_zip(full)
                        elif fn.endswith(".sym"):
                            self._add(full.replace("\\", "/").split("/"), (None, full))
            elif r.endswith(".zip") and os.path.exists(r):
                self._add_zip(r)

    def _add_zip(self, path):
        try:
            zf = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile):
            return
        self.zips.append(zf)
        for n in zf.namelist():
            if n.endswith(".sym"):
                self._add(n.split("/"), (zf, n))

    def _add(self, parts, where):
        if len(parts) >= 3:
            key = (parts[-3].lower(), parts[-2].upper())
            self.index[key] = where
            self.by_name.setdefault(key[0], []).append(key[1])

    def module_for(self, lib):
        dbg = (lib.get("debugName") or "").lower()
        bid = (lib.get("breakpadId") or "").upper()
        key = (dbg, bid)
        if key not in self.index:
            miss = self.stats["module not in the symbols"]
            tag = "%s %s (the symbols have %s)" % (lib.get("name"), bid or "no id", ",".join(self.by_name.get(dbg, [])[:2]) or "no such module")
            miss[tag] = miss.get(tag, 0) + 1
            return None
        if key not in self.mods:
            zf, name = self.index[key]
            path = name
            if zf is not None:
                if self.tmp is None:
                    self.tmp = tempfile.mkdtemp(prefix="hunt-sym-")
                path = zf.extract(name, self.tmp)
            self.mods[key] = SymModule(path)
            self.stats["modules parsed"] += 1
        return self.mods[key]

    def resolve(self, lib, rva):
        m = self.module_for(lib)
        if m is None:
            return None
        name = m.lookup(rva)
        if name:
            self.stats["resolved"] += 1
        else:
            self.stats["no record at the address"] += 1
        return name

    def close(self):
        for m in self.mods.values():
            m.close()
        for z in self.zips:
            z.close()
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)


class Thread:
    """One thread of a profile with its stacks resolved to function names."""

    symbolizer = None  # a Symbols: set before the Threads that should have their native frames named are made

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
            name, libname = self._native(int(loc, 16))
            kind, base = "native", ""
            key = name if libname is None else "%s [native:%s]" % (name, libname)
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
        """-> (name, lib name): the lib name only when the symbols named the frame, else the 'lib+0xoff (unsymbolicated)' text and None"""
        for lib in self.libs:
            try:
                start, end = int(lib.get("start", 0)), int(lib.get("end", 0))
            except (TypeError, ValueError):
                continue
            if start <= addr < end:
                libname = lib.get("name") or lib.get("debugName") or "?"
                if Thread.symbolizer is not None:
                    got = Thread.symbolizer.resolve(lib, addr - start)
                    if got:
                        return got, libname
                return "%s+0x%x (unsymbolicated)" % (libname, addr - start + int(lib.get("offset", 0))), None
        return "0x%x (unsymbolicated, no library)" % addr, None

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


def probe(path, symbols=None):
    if symbols:
        Thread.symbolizer = Symbols(symbols)
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
    raw = []
    for st in list(set(x for x in t.s_stack if x is not None))[:300]:
        ch = t.chain(st)
        if ch and t.fr_loc is not None and re.match(r"^0x", t.string(t.fr_loc[ch[0]])):
            raw.append(t.string(t.fr_loc[ch[0]]))
    print("native leaf locations as the file has them (first 5): %s; libs: %s" % (raw[:5], [(l.get("name"), hex(int(l.get("start", 0))), l.get("breakpadId")) for l in t.libs[:3]]))
    if Thread.symbolizer is not None:
        print("symbols: %s" % dict(Thread.symbolizer.stats))
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
    # Breakpad symbols: a zip with xul.pdb/<id>/xul.sym, FUNC and PUBLIC records, a line record, a module that is not in it
    d = tempfile.mkdtemp()
    zp = os.path.join(d, "syms.zip")
    sym = ("MODULE windows x86_64 ABCDEF0123456789ABCDEF012345678901 xul.pdb\nFILE 0 a.cpp\n"
           "FUNC 1000 40 0 mozilla::dom::Foo::Bar(int, char) const\n1000 10 5 0\nFUNC m 1100 20 0 nsThread::ProcessNextEvent(bool, bool*)\n"
           "PUBLIC 2000 0 WaitForMultipleObjectsEx\nPUBLIC m 3000 0 StealthJugglerPipeConnected\n")
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("xul.pdb/ABCDEF0123456789ABCDEF012345678901/xul.sym", sym)
    ss = Symbols(zp)
    lib = {"name": "xul.dll", "debugName": "xul.pdb", "breakpadId": "abcdef0123456789abcdef012345678901", "start": 0x7ff600000000, "end": 0x7ff700000000, "offset": 0}
    assert ss.resolve(lib, 0x1010) == "mozilla::dom::Foo::Bar", ss.resolve(lib, 0x1010)
    assert ss.resolve(lib, 0x1105) == "nsThread::ProcessNextEvent"
    assert ss.resolve(lib, 0x2000) == "WaitForMultipleObjectsEx" and ss.resolve(lib, 0x3010) == "StealthJugglerPipeConnected"
    assert ss.resolve(lib, 0x10) is None and ss.stats["modules parsed"] == 1
    assert ss.resolve(dict(lib, breakpadId="00" * 17), 0x1010) is None and ss.stats["module not in the symbols"]
    Thread.symbolizer = ss
    try:
        t = parent_main_thread(old)  # the lib has no debugName / breakpadId yet: stays unsymbolicated, no error
        assert t.frame_info(t.chain(3)[0])[0].startswith("xul.dll+0x1010 (unsymbolicated)")
        old["libs"][0].update({"debugName": "xul.pdb", "breakpadId": "ABCDEF0123456789ABCDEF012345678901"})
        t = parent_main_thread(old)
        assert t.frame_info(t.chain(3)[0])[0] == "mozilla::dom::Foo::Bar [native:xul.dll]", t.frame_info(t.chain(3)[0])
        assert short_name("operator()(int)") == "operator()" and short_name("a::b<T>::c(x) const") == "a::b<T>::c"
    finally:
        Thread.symbolizer = None
        ss.close()
    print("selftest ok")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    if len(sys.argv) > 2 and sys.argv[1] == "--probe":
        sym = sys.argv[sys.argv.index("--symbols") + 1] if "--symbols" in sys.argv else None
        sys.exit(probe(sys.argv[2], sym))
    print(__doc__)
    sys.exit(2)
