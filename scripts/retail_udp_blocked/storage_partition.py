r"""Storage partitioning on retail Firefox (Windows): what a site stores while it is embedded in another site, and whether the same
site sees it again as a top page (and the reverse). A measurement, written to be the reference of a test: it reports values and
never judges them.

Two SITES on one local server, the same port: A = http://127.0.0.1:<port>, B = http://localhost:<port> (different hosts, so
different sites). Fresh profile, default prefs, no proxy, the same driver options as webrtc_gather.py. Steps of one repetition:
  0. baseline: A's page with an iframe of B, read only: what B's frame sees in a fresh profile
  1. A's page embeds B's frame; inside the frame B sets a cookie by Set-Cookie header (hdr_frame), one by document.cookie
     (js_frame), a localStorage item (ls_frame) and a sessionStorage item (ss_frame), then reports by postMessage what it reads back
  2. B as a top page in a NEW tab, read only: document.cookie, localStorage, sessionStorage   -> "B as top page sees what B stored in a frame of A"
  3. B as a top page writes hdr_top (header), js_top (document.cookie), ls_top (localStorage), ss_top (sessionStorage)
  4. A's page again in the first tab, B's frame reads only                                    -> "B in a frame of A sees what B stored as a top page"
  5. the prefs that decide it, read through the driver (chrome context; needs geckodriver --allow-system-access, as in h3_fallback.py):
     network.cookie.cookieBehavior, privacy.partition.network_state, network.cookie.cookieBehavior.optInPartitioning (if present),
     privacy.firstparty.isolate, and the other privacy.partition.* / network.cookie.cookieBehavior.* ones found in the pref tree
sessionStorage belongs to one top-level browsing context, so a new tab never sees the one of another tab whatever the partitioning is;
it is recorded and left out of the yes/no.

Output under --out/partition/: result.json (meta, prefs, the repetitions, the answers, whether the repetitions agree) and
meta.json (versions and sha256, as the other scripts). The answers are also appended to $GITHUB_STEP_SUMMARY when it is set.
Exit code 1 only when a repetition broke (the driver, a page that never reported); never because of a value.
"""
import argparse, collections, configparser, datetime, hashlib, http.server, json, os, platform, socket, sys, threading, time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PREFS = [
    "network.cookie.cookieBehavior",
    "privacy.partition.network_state",
    "network.cookie.cookieBehavior.optInPartitioning",
    "privacy.firstparty.isolate",
]
PREF_BRANCHES = ["privacy.partition.", "network.cookie.cookieBehavior", "privacy.dynamic_firstparty."]
FRAME_COOKIES = {"cookie_header": "hdr_frame", "cookie_js": "js_frame"}
TOP_COOKIES = {"cookie_header": "hdr_top", "cookie_js": "js_top"}


# ---- pure parts -------------------------------------------------------------------------------------------------

def parse_cookie_string(s):
    """'a=1; b=2' -> {'a': '1', 'b': '2'} (what document.cookie returns)."""
    out = {}
    for part in (s or "").split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition("=")
        out[name.strip()] = value
    return out


def sees(report, writer):
    """Which of the items written by `writer` ('frame' or 'top') a page report shows back: header cookie, document.cookie
    cookie, localStorage item. sessionStorage is reported apart (see the module comment)."""
    names = FRAME_COOKIES if writer == "frame" else TOP_COOKIES
    cookies = parse_cookie_string((report or {}).get("cookie"))
    ls = (report or {}).get("localStorage") or {}
    return {
        "cookie_header": names["cookie_header"] in cookies,
        "cookie_js": names["cookie_js"] in cookies,
        "localStorage": ("ls_" + writer) in ls,
        "sessionStorage": ("ss_" + writer) in ((report or {}).get("sessionStorage") or {}),
    }


def answer(seen):
    """'yes' when every counted item is seen, 'no' when none is, else 'partly: <the ones seen>'. sessionStorage is not counted."""
    counted = {k: v for k, v in seen.items() if k != "sessionStorage"}
    if all(counted.values()):
        return "yes"
    if not any(counted.values()):
        return "no"
    return "partly: " + ", ".join(k for k, v in counted.items() if v)


def summary_lines(answers, prefs_by_name, agree_info):
    return [
        "B as top page sees what B stored in a frame of A: %s" % answers["top_sees_frame"],
        "B in a frame of A sees what B stored as a top page: %s" % answers["frame_sees_top"],
        "prefs: " + ", ".join("%s=%s" % (k, ("absent" if not v.get("present") else v.get("value"))) for k, v in prefs_by_name.items()),
        agree_info,
    ]


def agreement(sessions):
    """Do the repetitions agree? Compared on the answers, the item-by-item sightings and the prefs."""
    good = [s for s in sessions if not s.get("error")]
    keys = collections.Counter(json.dumps({"top_sees_frame": s["top_sees_frame"], "frame_sees_top": s["frame_sees_top"],
                                           "frame_sees_own": s["frame_sees_own"], "prefs": s["prefs_values"]}, sort_keys=True)
                               for s in good)
    return {"repetitions": len(sessions), "ok": len(good), "distinct_outcomes": len(keys),
            "all_agree": len(keys) == 1 and len(good) == len(sessions),
            "outcome_counts": [{"count": c, "outcome": json.loads(k)} for k, c in keys.most_common()]}


# ---- the pages ---------------------------------------------------------------------------------------------------

def page_html(role, mode):
    """The page of site B, as a frame (role 'frame') or as a top page (role 'top'). mode 'write' stores, 'read' only reads."""
    return """<!doctype html><html><head><meta charset="utf-8"><title>B %(role)s %(mode)s</title></head><body><pre id="out">starting</pre><script>
const role = %(role)r, mode = %(mode)r;
const safe = (f) => { try { return f(); } catch (e) { return {error: String(e)}; } };
const dump = (s) => safe(() => { const o = {}; for (let i = 0; i < s.length; i++) { const k = s.key(i); o[k] = s.getItem(k); } return o; });
if (mode === 'write') {
  safe(() => { document.cookie = 'js_' + role + '=1; path=/'; });
  safe(() => localStorage.setItem('ls_' + role, '1'));
  safe(() => sessionStorage.setItem('ss_' + role, '1'));
}
const rep = {kind: role, mode: mode, origin: location.origin, top: window.top === window,
  cookie: safe(() => document.cookie), localStorage: dump(window.localStorage), sessionStorage: dump(window.sessionStorage)};
window.__report = rep;
document.getElementById('out').textContent = JSON.stringify(rep, null, 1);
if (window.parent !== window) window.parent.postMessage(rep, '*');
</script></body></html>
""" % {"role": role, "mode": mode}


def a_page(frame_url):
    """The page of site A: an iframe of site B and a list of what the frame reported."""
    return """<!doctype html><html><head><meta charset="utf-8"><title>A</title></head><body>
<iframe id="b" src="%s" width="400" height="200"></iframe>
<script>
window.__reports = [];
window.addEventListener('message', (e) => window.__reports.push({origin: e.origin, data: e.data}));
</script></body></html>
""" % frame_url


def route(path, port):
    """(status, headers, body) for one request; the same answer whatever the Host header says."""
    u = urlsplit(path)
    q = parse_qs(u.query)
    mode = (q.get("mode") or ["read"])[0]
    if mode not in ("read", "write"):
        return 400, [], "bad mode"
    headers = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")]
    if u.path == "/a.html":
        return 200, headers, a_page("http://localhost:%d/frame.html?mode=%s" % (port, mode))
    if u.path in ("/frame.html", "/top.html"):
        role = "frame" if u.path == "/frame.html" else "top"
        if mode == "write":
            headers.append(("Set-Cookie", "hdr_%s=1; Path=/" % role))
        return 200, headers, page_html(role, mode)
    return 404, [], "not found"


# ---- the server and the browser ---------------------------------------------------------------------------------

def serve():
    """One handler on 127.0.0.1 and, when the machine allows, on ::1 with the same port. Returns (servers, port)."""
    holder = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = route(self.path, holder["port"])
            data = body.encode("utf-8")
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    v4 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = v4.server_address[1]
    holder["port"] = port
    servers = [v4]
    try:
        class V6(http.server.ThreadingHTTPServer):
            address_family = socket.AF_INET6
        servers.append(V6(("::1", port), H))
    except OSError:
        pass
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    return servers, port


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def firefox_version(firefox):
    ini = configparser.ConfigParser()
    ini.read(Path(firefox).parent / "application.ini", encoding="utf-8")
    return "Mozilla Firefox %s" % ini.get("App", "Version", fallback="?")


PREFS_JS = """
const names = arguments[0], branches = arguments[1];
const out = {named: {}, tree: {}};
const rd = (n) => {
  const t = Services.prefs.getPrefType(n);   // 0 invalid, 32 string, 64 int, 128 bool
  if (t === 0) return {present: false};
  const v = t === 32 ? Services.prefs.getStringPref(n) : t === 64 ? Services.prefs.getIntPref(n) : Services.prefs.getBoolPref(n);
  return {present: true, value: v, user_value: Services.prefs.prefHasUserValue(n)};
};
for (const n of names) { try { out.named[n] = rd(n); } catch (e) { out.named[n] = {error: String(e)}; } }
for (const b of branches) {
  try { for (const n of Services.prefs.getChildList(b)) { try { out.tree[n] = rd(n); } catch (e) { out.tree[n] = {error: String(e)}; } } } catch (e) {}
}
return out;
"""


def wait_report(driver, js, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        v = driver.execute_script(js)
        if v:
            return v
        time.sleep(0.25)
    raise TimeoutError("the page never reported: " + js)


def frame_report(driver, url):
    driver.get(url)
    got = wait_report(driver, "return window.__reports.length ? window.__reports.slice() : null")
    return got[-1]


def one_session(firefox, geckodriver, port, wait_s):
    import psutil
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    A = "http://127.0.0.1:%d" % port
    B = "http://localhost:%d" % port
    opts = Options()
    opts.binary_location = firefox
    opts.add_argument("-headless")
    driver = webdriver.Firefox(options=opts, service=Service(executable_path=geckodriver, service_args=["--allow-system-access"]))
    gecko = driver.service.process.pid
    rec = {"A": A, "B": B}
    try:
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
        first = driver.current_window_handle
        rec["baseline_frame"] = frame_report(driver, A + "/a.html?mode=read")
        rec["frame_write"] = frame_report(driver, A + "/a.html?mode=write")
        driver.switch_to.new_window("tab")
        driver.get(B + "/top.html?mode=read")
        rec["top_read"] = driver.execute_script("return window.__report")
        driver.get(B + "/top.html?mode=write")
        rec["top_write"] = driver.execute_script("return window.__report")
        driver.close()
        driver.switch_to.window(first)
        rec["frame_read_after_top"] = frame_report(driver, A + "/a.html?mode=read")
        driver.set_context("chrome")
        try:
            rec["prefs"] = driver.execute_script(PREFS_JS, PREFS, PREF_BRANCHES)
        finally:
            driver.set_context("content")
    finally:
        try:
            kids = [c.pid for c in psutil.Process(gecko).children(recursive=True)]
        except psutil.NoSuchProcess:
            kids = []
        try:
            driver.quit()
        except Exception:
            pass
        for pid in kids:
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass
        time.sleep(wait_s)
    return rec


def analyse(rec):
    """The pure part of a repetition: what the reports say."""
    fw = (rec.get("frame_write") or {}).get("data")
    own = sees(fw, "frame")
    top_sees_frame = sees(rec.get("top_read"), "frame")
    frame_sees_top = sees((rec.get("frame_read_after_top") or {}).get("data"), "top")
    named = ((rec.get("prefs") or {}).get("named")) or {}
    return {
        "frame_sees_own": own, "top_sees_frame": top_sees_frame, "frame_sees_top": frame_sees_top,
        "top_sees_frame_answer": answer(top_sees_frame), "frame_sees_top_answer": answer(frame_sees_top),
        "baseline_frame_sees_anything": any(sees((rec.get("baseline_frame") or {}).get("data"), w)[k]
                                            for w in ("frame", "top") for k in ("cookie_header", "cookie_js", "localStorage")),
        "prefs_values": {k: (v.get("value") if v.get("present") else None) for k, v in sorted(named.items())},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--firefox", default=os.environ.get("FIREFOX_BIN"))
    ap.add_argument("--geckodriver", default=os.environ.get("GECKODRIVER_BIN"))
    a = ap.parse_args(argv)
    if not a.firefox or not a.geckodriver:
        raise SystemExit("set --firefox/--geckodriver or FIREFOX_BIN/GECKODRIVER_BIN")
    out = Path(a.out) / "partition"
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    servers, port = serve()
    sessions, broke = [], 0
    try:
        for n in range(1, a.runs + 1):
            try:
                rec = one_session(a.firefox, a.geckodriver, port, 1)
                rec.update(analyse(rec))
                print("run %d: top sees frame: %s; frame sees top: %s" % (n, rec["top_sees_frame_answer"], rec["frame_sees_top_answer"]), flush=True)
            except Exception as e:
                broke += 1
                rec = {"error": "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:300] if str(e) else "")}
                print("::error::storage partition run %d broke: %s" % (n, rec["error"]), flush=True)
            rec["run"] = n
            sessions.append(rec)
    finally:
        for s in servers:
            s.shutdown()
    good = [s for s in sessions if not s.get("error")]
    first = good[0] if good else None
    agree = agreement(sessions)
    answers = {"top_sees_frame": first["top_sees_frame_answer"] if first else None,
               "frame_sees_top": first["frame_sees_top_answer"] if first else None}
    named = (((first or {}).get("prefs") or {}).get("named")) or {}
    lines = summary_lines(answers, named, "%d of %d repetitions agree on answers, sightings and prefs (%d distinct outcome%s)" % (
        max((c["count"] for c in agree["outcome_counts"]), default=0), agree["repetitions"], agree["distinct_outcomes"],
        "" if agree["distinct_outcomes"] == 1 else "s")) if first else ["storage partition: no repetition completed"]
    result = {
        "meta": {"what": "storage partitioning on retail Firefox, two sites on a local server (A = 127.0.0.1, B = localhost), fresh profile per repetition, default prefs",
                 "firefox_version": firefox_version(a.firefox), "firefox_sha256": sha256(a.firefox),
                 "xul_dll_sha256": sha256(Path(a.firefox).parent / "xul.dll"), "geckodriver_sha256": sha256(a.geckodriver),
                 "date_started": started, "machine": platform.platform(), "port": port, "runs": a.runs,
                 "command": " ".join(["python"] + sys.argv)},
        "answers": answers, "agreement": agree, "prefs_first_repetition": ((first or {}).get("prefs")),
        "summary_lines": lines, "repetitions": sessions,
    }
    (out / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({**result["meta"], "date": started}, indent=1), encoding="utf-8")
    md = "### Storage partitioning (retail Firefox, UDP free)\n\n" + "\n".join("- " + ln for ln in lines) + "\n"
    print(md, flush=True)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(md)
    if broke:
        sys.exit(1)


if __name__ == "__main__":
    main()
