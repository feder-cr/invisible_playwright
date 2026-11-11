r"""HTTP/3 on retail Firefox (Windows): what a page reads (nextHopProtocol) and how long the load takes, visit after visit, on a
network that drops UDP (the case "a proxy without UDP" must look like) and, as a control, on a network that does not.

Ported from the Linux reference script h3_fallback.py of the private study repo (captures/h3-fallback/retail-159-linux).
The probe, the sites, the per-visit record and the output fields are unchanged. Adapted for Windows:
  - the Firefox and geckodriver paths come from --firefox / --geckodriver (or FIREFOX_BIN / GECKODRIVER_BIN), not /opt and /usr/local;
  - `firefox.exe --version` prints nothing to a pipe on Windows, so the version is read from application.ini next to the binary;
  - libxul.so becomes xul.dll (the meta key stays "libxul_sha256" so a Linux and a Windows capture compare field by field);
  - the code is behind main(), so the test suite can import the helpers.

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
  transferSize: n.transferSize, resources: c, url: location.href} : null;
"""


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


def visit_series(url, firefox, geckodriver, visits_n):
    import psutil
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    opts = Options()
    opts.binary_location = firefox
    opts.add_argument("-headless")
    driver = webdriver.Firefox(options=opts, service=Service(executable_path=geckodriver))
    gecko = driver.service.process.pid
    visits = []
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
    return visits


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--visits", type=int, default=3)
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--firefox", default=os.environ.get("FIREFOX_BIN"))
    ap.add_argument("--geckodriver", default=os.environ.get("GECKODRIVER_BIN"))
    a = ap.parse_args(argv)
    if not a.firefox or not a.geckodriver:
        raise SystemExit("set --firefox/--geckodriver or FIREFOX_BIN/GECKODRIVER_BIN")

    started = datetime.datetime.now().astimezone()
    out = Path(a.out) / a.label
    summary = []
    for name in [s for s in a.sites.split(",") if s]:
        url = SITES[name]
        for run in range(1, a.runs + 1):
            print("%s run %d/%d" % (name, run, a.runs), flush=True)
            visits = visit_series(url, a.firefox, a.geckodriver, a.visits)
            d = out / name
            d.mkdir(parents=True, exist_ok=True)
            (d / ("run%d.json" % run)).write_text(json.dumps({"site": name, "url": url, "run": run, "visits": visits}, indent=1), encoding="utf-8")
            summary.append({"site": name, "run": run,
                            "protocols": [(v.get("nav") or {}).get("nextHopProtocol") for v in visits],
                            "wall_ms": [v.get("wall_ms") for v in visits],
                            "responseStart_ms": [round((v.get("nav") or {}).get("responseStart", 0) - (v.get("nav") or {}).get("fetchStart", 0)) if v.get("nav") else None for v in visits],
                            "errors": [v.get("error") for v in visits]})
            print("   ", summary[-1]["protocols"], summary[-1]["wall_ms"], flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (out / "meta.json").write_text(json.dumps({
        "axis": "retail %s Windows, HTTP/3 and the fallback" % firefox_version(a.firefox).rsplit(" ", 1)[-1],
        "label": a.label, "date": started.isoformat(timespec="seconds"),
        "firefox": firefox_version(a.firefox), "libxul_sha256": sha256(Path(a.firefox).parent / "xul.dll"),
        "geckodriver_sha256": sha256(a.geckodriver),
        "sites": {n: SITES[n] for n in a.sites.split(",") if n}, "runs": a.runs, "visits": a.visits,
        "block": os.environ.get("UDP_BLOCK", "?"), "machine": platform.platform(), "command": " ".join(["python"] + sys.argv),
    }, indent=1), encoding="utf-8")
    print("done", flush=True)


if __name__ == "__main__":
    main()
