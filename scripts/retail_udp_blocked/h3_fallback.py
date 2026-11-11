r"""HTTP/3 on retail Firefox (Windows): what a page reads (nextHopProtocol) and how long the load takes, visit after visit, on a
network that drops UDP (the case "a proxy without UDP" must look like) and, as a control, on a network that does not.

Ported from the Linux reference script h3_fallback.py of the private study repo (captures/h3-fallback/retail-159-linux).
The probe, the sites, the per-visit record and the output fields are unchanged. Adapted for Windows:
  - the Firefox and geckodriver paths come from --firefox / --geckodriver (or FIREFOX_BIN / GECKODRIVER_BIN), not /opt and /usr/local;
  - `firefox.exe --version` prints nothing to a pipe on Windows, so the version is read from application.ini next to the binary;
  - libxul.so becomes xul.dll (the meta key stays "libxul_sha256" so a Linux and a Windows capture compare field by field);
  - the code is behind main(), so the test suite can import the helpers;
  - --pref NAME=VALUE (repeatable) sets an extra Firefox pref in the fresh profile; the DoH conditions use it for
    network.trr.mode=3 plus the provider the product declares (see run_condition.ps1);
  - the navigation record also carries loadEventEnd and domContentLoadedEventEnd (the load time of the visit as the page sees it);
  - at the end of each session the DNS state is read through the driver (chrome context, needs geckodriver --allow-system-access,
    which adds Firefox's --remote-allow-system-access): the network.trr prefs, the DNS service's current TRR mode, a fresh lookup of the
    site's host with its record's TRR flag, and the DNS cache entries of about:networking#dns with their `trr` flag. It is saved
    in each run file as "dns_evidence". With --require-doh a session whose evidence does not show DoH in use makes the
    run exit 1 (after all files are written): the condition is then not the one it claims to be.

For each site and each run (a fresh profile per run, no proxy): VISITS visits of the same origin in one session. The first
visit learns the Alt-Svc; the later ones may try h3. A query string (?v=N) keeps every visit off the HTTP cache so each one
goes to the network.

Per visit: wall time of driver.get() (to the load event, the Selenium default), the navigation entry (nextHopProtocol,
fetchStart, domainLookupEnd, connectStart, connectEnd, secureConnectionStart, requestStart, responseStart, responseEnd, duration,
transferSize) and the nextHopProtocol of the first resources.

Output under --out/<label>/<site>/run<N>.json and --out/<label>/summary.json; --out/<label>/meta.json (versions, sha256, command).
"""
import argparse, configparser, datetime, hashlib, json, os, platform, sys, time
from pathlib import Path
from urllib.parse import urlsplit

SITES = {
    "cloudflare-quic": "https://cloudflare-quic.com/",
    "google": "https://www.google.com/",
    "facebook": "https://www.facebook.com/",
}
NAV_JS = """
const n = performance.getEntriesByType('navigation')[0];
const r = performance.getEntriesByType('resource').slice(0, 30).map(e => e.nextHopProtocol);
const c = {};
for (const p of r) c[p || '(empty)'] = (c[p || '(empty)'] || 0) + 1;
return n ? {nextHopProtocol: n.nextHopProtocol, fetchStart: n.fetchStart, domainLookupEnd: n.domainLookupEnd,
  connectStart: n.connectStart, connectEnd: n.connectEnd, secureConnectionStart: n.secureConnectionStart,
  requestStart: n.requestStart, responseStart: n.responseStart, responseEnd: n.responseEnd, duration: n.duration,
  loadEventEnd: n.loadEventEnd, domContentLoadedEventEnd: n.domContentLoadedEventEnd,
  transferSize: n.transferSize, resources: c, url: location.href} : null;
"""

# Chrome-context script: DNS state of the browser. arguments[0] = host name, last argument = the async callback.
DNS_EVIDENCE_JS = """
const done = arguments[arguments.length - 1];
const host = arguments[0];
const Cc = Components.classes, Ci = Components.interfaces;
const rd = (f) => { try { return f(); } catch (e) { return 'error: ' + e; } };
const out = {host: host};
out.prefs = {
  'network.trr.mode': rd(() => Services.prefs.getIntPref('network.trr.mode')),
  'network.trr.uri': rd(() => Services.prefs.getStringPref('network.trr.uri')),
  'network.trr.bootstrapAddr': rd(() => Services.prefs.getStringPref('network.trr.bootstrapAddr')),
};
out.dns_service = {currentTrrMode: rd(() => Services.dns.currentTrrMode),
  currentTrrURI: rd(() => Services.dns.currentTrrURI)};
const dashboard = () => {
  try {
    Cc['@mozilla.org/network/dashboard;1'].getService(Ci.nsIDashboard).requestDNSInfo(data => {
      out.cache_entries = (data.entries || []).map(e => ({hostname: e.hostname, trr: e.trr, family: e.family,
        flags: e.flags, addresses: (e.hostaddr || []).length}));
      done(out);
    });
  } catch (e) { out.cache_error = String(e); done(out); }
};
try {
  const listener = {
    onLookupComplete(req, rec, status) {
      out.lookup = {status: status};
      try {
        const r = rec.QueryInterface(Ci.nsIDNSAddrRecord);
        out.lookup.isTRR = rd(() => r.IsTRR);
        out.lookup.effectiveTRRMode = rd(() => r.effectiveTRRMode);
        out.lookup.trrFetchDuration = rd(() => r.trrFetchDuration);
        const addrs = [];
        try { while (true) addrs.push(r.getNextAddrAsString()); } catch (e) {}
        out.lookup.addresses = addrs.length;
      } catch (e) { out.lookup.error = String(e); }
      dashboard();
    },
    QueryInterface: ChromeUtils.generateQI(['nsIDNSListener']),
  };
  Services.dns.asyncResolve(host, Ci.nsIDNSService.RESOLVE_TYPE_DEFAULT, Ci.nsIDNSService.RESOLVE_BYPASS_CACHE,
    null, listener, Services.tm.mainThread, {});
} catch (e) { out.lookup = {error: String(e)}; dashboard(); }
"""


def doh_proven(ev, host):
    """(True, why) when the evidence shows DoH in use for this host: mode 3 as the browser holds it, and either the fresh
    lookup's record says TRR or the DNS cache entry of the host (about:networking#dns) has trr set."""
    if not isinstance(ev, dict) or "error" in ev:
        return False, "no evidence read (%s)" % (ev.get("error") if isinstance(ev, dict) else ev)
    mode = (ev.get("prefs") or {}).get("network.trr.mode")
    if mode != 3:
        return False, "network.trr.mode is %r, not 3" % (mode,)
    cur = (ev.get("dns_service") or {}).get("currentTrrMode")
    if isinstance(cur, int) and cur != 3:
        return False, "the DNS service's current TRR mode is %r, not 3" % (cur,)
    if (ev.get("lookup") or {}).get("isTRR") is True:
        return True, "mode 3 and the fresh lookup of %s is a TRR record" % host
    for e in ev.get("cache_entries") or []:
        if e.get("hostname") == host and e.get("trr") is True:
            return True, "mode 3 and the DNS cache entry of %s has trr" % host
    return False, "mode 3, but neither the fresh lookup nor a DNS cache entry of %s shows TRR" % host


def parse_pref(text):
    """NAME=VALUE; VALUE is JSON when it parses (true, 3, "x"), a plain string otherwise."""
    name, sep, raw = text.partition("=")
    if not name or not sep:
        raise SystemExit("--pref wants NAME=VALUE, got %r" % text)
    try:
        return name, json.loads(raw)
    except ValueError:
        return name, raw


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def firefox_version(firefox):
    """'Mozilla Firefox 159.0' from application.ini beside the binary (no --version on Windows)."""
    ini = configparser.ConfigParser()
    ini.read(Path(firefox).parent / "application.ini", encoding="utf-8")
    return "Mozilla Firefox %s" % ini.get("App", "Version", fallback="?")


def visit_series(url, firefox, geckodriver, visits_n, prefs=None):
    import psutil
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    opts = Options()
    opts.binary_location = firefox
    opts.add_argument("-headless")
    for k, v in (prefs or {}).items():
        opts.set_preference(k, v)
    # chrome-context access (for the DNS evidence) needs system access on Firefox >= 138; the flag is the same in every condition
    driver = webdriver.Firefox(options=opts, service=Service(executable_path=geckodriver, service_args=["--allow-system-access"]))
    gecko = driver.service.process.pid
    visits = []
    evidence = None
    try:
        driver.set_page_load_timeout(120)
        for v in range(1, visits_n + 1):
            target = url if v == 1 else url + ("&" if "?" in url else "?") + "v=%d" % v
            t0 = time.time()
            rec = {"visit": v, "url": target}
            try:
                driver.get(target)
                rec["wall_ms"] = round((time.time() - t0) * 1000)
                time.sleep(2)   # let the later resources and the Alt-Svc settle
                rec["nav"] = driver.execute_script(NAV_JS)
            except Exception as e:
                rec["wall_ms"] = round((time.time() - t0) * 1000)
                rec["error"] = "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:200])
            visits.append(rec)
        try:
            driver.set_script_timeout(30)
            driver.set_context("chrome")
            try:
                evidence = driver.execute_async_script(DNS_EVIDENCE_JS, urlsplit(url).hostname)
            finally:
                driver.set_context("content")
        except Exception as e:
            evidence = {"error": "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:200] if str(e) else "")}
    finally:
        try:
            kids = psutil.Process(gecko).children(recursive=True)
        except psutil.NoSuchProcess:
            kids = []
        try:
            driver.quit()
        except Exception:
            pass
        for p in kids:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                pass
        time.sleep(1)
    return visits, evidence


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--visits", type=int, default=3)
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pref", action="append", default=[], metavar="NAME=VALUE", help="extra Firefox pref (repeatable)")
    ap.add_argument("--require-doh", action="store_true", help="exit 1 when a session's DNS evidence does not show DoH in use")
    ap.add_argument("--firefox", default=os.environ.get("FIREFOX_BIN"))
    ap.add_argument("--geckodriver", default=os.environ.get("GECKODRIVER_BIN"))
    a = ap.parse_args(argv)
    if not a.firefox or not a.geckodriver:
        raise SystemExit("set --firefox/--geckodriver or FIREFOX_BIN/GECKODRIVER_BIN")

    prefs = dict(parse_pref(t) for t in a.pref)
    started = datetime.datetime.now().astimezone()
    doh_failures = []
    out = Path(a.out) / a.label
    summary = []
    for name in [s for s in a.sites.split(",") if s]:
        url = SITES[name]
        for run in range(1, a.runs + 1):
            print("%s run %d/%d" % (name, run, a.runs), flush=True)
            visits, evidence = visit_series(url, a.firefox, a.geckodriver, a.visits, prefs)
            host = urlsplit(url).hostname
            doh_ok, doh_why = doh_proven(evidence, host)
            if a.require_doh and not doh_ok:
                doh_failures.append("%s run %d: %s" % (name, run, doh_why))
                print("::error::DoH not shown in use: %s run %d: %s" % (name, run, doh_why), flush=True)
            d = out / name
            d.mkdir(parents=True, exist_ok=True)
            (d / ("run%d.json" % run)).write_text(json.dumps({"site": name, "url": url, "run": run, "visits": visits,
                                                           "dns_evidence": evidence, "doh_proven": doh_ok, "doh_proof": doh_why}, indent=1), encoding="utf-8")
            summary.append({"site": name, "run": run,
                            "protocols": [(v.get("nav") or {}).get("nextHopProtocol") for v in visits],
                            "wall_ms": [v.get("wall_ms") for v in visits],
                            "responseStart_ms": [round((v.get("nav") or {}).get("responseStart", 0) - (v.get("nav") or {}).get("fetchStart", 0)) if v.get("nav") else None for v in visits],
                            "errors": [v.get("error") for v in visits],
                            "loadEventEnd_ms": [round((v.get("nav") or {}).get("loadEventEnd", 0)) if v.get("nav") else None for v in visits],
                            "doh_proven": doh_ok})
            print("   ", summary[-1]["protocols"], summary[-1]["wall_ms"], flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "axis": "retail %s Windows, HTTP/3 and the fallback" % firefox_version(a.firefox).rsplit(" ", 1)[-1],
        "label": a.label, "date": started.isoformat(timespec="seconds"),
        "firefox": firefox_version(a.firefox), "libxul_sha256": sha256(Path(a.firefox).parent / "xul.dll"),
        "geckodriver_sha256": sha256(a.geckodriver),
        "sites": {n: SITES[n] for n in a.sites.split(",") if n}, "runs": a.runs, "visits": a.visits,
        "block": os.environ.get("UDP_BLOCK", "?"), "prefs": prefs, "require_doh": a.require_doh,
        "doh_failures": doh_failures, "machine": platform.platform(), "command": " ".join(["python"] + sys.argv),
    }, indent=1), encoding="utf-8")
    print("done", flush=True)
    if doh_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
