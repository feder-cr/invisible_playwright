"""A small multi-page fake news site for the "user journey" profile (P4).

One process serves two origins: "A" (the site) and "O" (the other origin, used for a partner page and for the
ad network noise). The two origins are two different SITES for Firefox: A is 127.0.0.1:<port>, O is localhost:<port>. Two ports of one
host would be the same site, and with Fission an "other origin" frame of the same site never leaves the page's process;
with two sites the ad frames and the partner page are really out of process. Cookies and sign-in stay on A.
Every page also runs a deterministic ad noise (DET_JS): every slow
response is held by the server until an event releases it, never by a timer, and nothing is aborted or
removed while a request is in flight, so stock Firefox gives the same outcome on every run. Two pages stay
"not idle" on purpose (PARK_PAGES, PARK_KEEPALIVE_PAGES). Add ?noise=0 to a URL to turn the noise off
(every internal link and form keeps the switch).

    from fakeweb import journey_site
    origin_a, origin_o, stop = journey_site.start((0, 0))

Pages (A): /  /category/<name>[?sort=new|top]  /article/<id>  /search[?q=..]
/signin (GET form, POST sets cookies, 303 to /account)  /account  /signout (POST)
/download/<id>.txt (Content-Disposition: attachment).  O: /partner/<id>.
Both origins also serve /r?kind=.. (finite resources), /hold, /release, /stream, /jf/<kind> and POST /beacon.
User: alice / secret.
"""
import html
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

from fakeweb import sites

CACHE_PAGES = "no-cache"
STATE = None   # the State of the running site (set by start): the walk tells it the current step
USER, PASSWORD = "alice", "secret"
N_ARTICLES = 30
CATEGORIES = ["world", "tech", "sport", "travel", "science", "culture"]
WORDS = ["Harbour", "Orchard", "Signal", "Lantern", "Meadow", "Circuit", "Glacier", "Market",
         "Village", "Satellite", "Garden", "Bridge", "Compass", "Library", "Station"]
PRELUDE = sites.PRELUDE

CSS = """
body{font:16px/1.5 sans-serif;margin:0 auto;max-width:900px;padding:0 16px}
header{display:flex;gap:24px;align-items:center;padding:12px 0;border-bottom:1px solid #ccc}
header nav a{margin-right:16px}
#logo{font-weight:bold;font-size:20px}
li{margin:6px 0}
button,input{font:inherit;padding:4px 8px}
.err{color:#a00}
"""


def title_of(i):
    return "Article %d: %s news" % (i, WORDS[i % len(WORDS)])


def sort_orders(ids):
    return {
        "default": list(ids),
        "new": sorted(ids, reverse=True),
        "top": sorted(ids, key=lambda i: ((i * 5) % 8, i)),
    }


def link(ctx, path, **params):
    items = [(k, v) for k, v in params.items() if v is not None]
    if not ctx.noise:
        items.insert(0, ("noise", "0"))
    return path + ("?" + urlencode(items) if items else "")


# Deterministic noise. Every slow response is held by the server until an event releases it (a /release
# call from the page, or the walk reaching a given step), never by a timer; nothing is aborted or removed
# while a request is in flight; the only things fired while leaving a page are one sendBeacon and one
# keepalive POST. A page that must stay "not idle" says so explicitly (see PARK_PAGES).
DET_JS = r"""
const tok = Math.random().toString(36).slice(2, 8);
const rel = (id) => get(A + '/release?id=' + id);
// 1. beacons and keepalive fetches
afterLoad(300, () => {
  navigator.sendBeacon(O + '/beacon?src=load&n=1', 'a=1');
  navigator.sendBeacon(A + '/beacon?src=load&n=2', new Blob(['{"x":1}'], {type: 'application/json'}));
});
fetch(O + '/r?kind=json&id=ka1', {keepalive: true, mode: 'no-cors'}).catch(noop);
afterLoad(200, () => fetch(O + '/r?kind=json&id=ka2', {keepalive: true, mode: 'no-cors'}).catch(noop));
afterLoad(250, () => fetch(O + '/beacon?src=load&n=3', {method: 'POST', keepalive: true, mode: 'no-cors', body: 'x'}).catch(noop));
// 2. tracking pixels (images are aborted by the test route)
for (let i = 0; i < 4; i++) { const im = new Image(); im.src = O + '/r?kind=png&id=px' + i; }
// 3. five @font-face rules on one font URL
afterLoad(250, () => {
  const l = document.createElement('link');
  l.rel = 'stylesheet'; l.href = O + '/r?kind=fontcss&id=fc';
  document.head.appendChild(l);
  for (const w of [400, 500, 600, 700, 800]) {
    const sp = document.createElement('span');
    sp.textContent = 'weight ' + w + ' ';
    sp.style.cssText = 'font-family: FakeFont, serif; font-weight: ' + w;
    document.body.appendChild(sp);
  }
});
// 4. redirect chains across origins
const rs = document.createElement('script');
rs.src = A + '/r?kind=js&redirect=3&id=rc1';
document.head.appendChild(rs);
get(O + '/r?kind=json&redirect=2&id=rc2');
// 5. a fetch chain (each one starts when the previous one is done, no timer); the long poll is released at its end
get(A + '/hold?id=lp' + tok);
let late = 0;
const nextLate = () => {
  if (++late > 7) { rel('lp' + tok); return; }
  get(A + '/r?kind=json&id=late' + late).then(nextLate);
};
nextLate();
// 6. a frame that fetches held responses, releases them itself, tells the parent, and is removed then
const sf = document.createElement('iframe');
sf.src = O + '/jf/slow?tok=' + tok;
document.body.appendChild(sf);
addEventListener('message', (e) => { if (e.data === 'slow-done' + tok) sf.remove(); });
// 7. three ad frames, each with a beacon, a pixel, a script and a nested frame
for (let i = 0; i < 3; i++) {
  const f = document.createElement('iframe');
  f.src = O + '/jf/ad?n=' + i + '&a=' + encodeURIComponent(A);
  document.body.appendChild(f);
}
// 8. late DOM resources
afterLoad(600, () => {
  const im = document.createElement('img');
  im.src = O + '/r?kind=svg&id=ld1';
  document.body.appendChild(im);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const image = document.createElementNS(ns, 'image');
  image.setAttribute('href', O + '/r?kind=png&id=ld2');
  svg.appendChild(image);
  document.body.appendChild(svg);
  const l = document.createElement('link');
  l.rel = 'stylesheet'; l.href = A + '/r?kind=css&id=ld3';
  document.head.appendChild(l);
});
// 9. aborts that cannot race: the server sends headers and part of the body, the page aborts on the headers
for (let i = 0; i < 3; i++) {
  const c = new AbortController();
  fetch(A + '/stream?id=ab' + i, {signal: c.signal}).then(() => { c.abort(); rel('stream-ab' + i); }).catch(noop);
}
// 10. leaving the page: one sendBeacon and one keepalive POST
addEventListener('pagehide', () => {
  navigator.sendBeacon(O + '/beacon?t=pagehide&n=1', 'bye');
  fetch(O + '/beacon?t=pagehide&n=2', {method: 'POST', keepalive: true, mode: 'no-cors', body: 'x'}).catch(noop);
});
"""

# Page that stays "not idle" on purpose: its held request is released when the walk reaches this step.
PARK_PAGES = {"category": 9}
# Same with a keepalive fetch (a request the client must still count as in flight): the search page, until step 13.
PARK_KEEPALIVE_PAGES = {"search": 13}


def noise_script(ctx, park_until=None, park_keepalive_until=None):
    if not ctx.noise:
        return ""
    js = DET_JS
    if park_until:
        js += "\nget(A + '/hold?id=park' + tok + '&until=%d');\n" % park_until
    if park_keepalive_until:
        js += ("\nfetch(A + '/hold?id=kapark' + tok + '&until=%d', {keepalive: true}).catch(noop);\n"
               % park_keepalive_until)
    # Own <script> element: a failure in the noise cannot break the page's script.
    return "<script>(function(){const A=%s,O=%s;\n%s\ntry{\n%s\n}catch(e){}\n})();</script>" % (
        json.dumps(ctx.own), json.dumps(ctx.other), PRELUDE, js)


JFRAME_JS = {
    "slow": """
const q = new URLSearchParams(location.search).get('tok');
const ids = [0, 1, 2].map((i) => 'sf' + q + i);
const ps = ids.map((id) => fetch('/hold?id=' + id).then((r) => r.text()).catch(() => {}));
for (let i = 0; i < 3; i++) { const im = new Image(); im.src = '/r?kind=png&id=si' + i; }
ids.forEach((id) => fetch('/release?id=' + id));   // a release that comes first is remembered by the server
Promise.all(ps).then(() => parent.postMessage('slow-done' + q, '*'));
""",
    "ad": """
const q = new URLSearchParams(location.search), n = q.get('n');
navigator.sendBeacon('/beacon?src=ad&n=' + n, 'ad=' + n);
const im = new Image(); im.src = '/r?kind=png&id=adpx' + n;
const s = document.createElement('script'); s.src = '/r?kind=js&id=ads' + n; document.head.appendChild(s);
const f = document.createElement('iframe'); f.src = q.get('a') + '/jf/nested?n=' + n; document.body.appendChild(f);
""",
    "nested": """
const n = new URLSearchParams(location.search).get('n');
const s = document.createElement('script'); s.src = '/r?kind=js&id=nest' + n; document.head.appendChild(s);
""",
}


def jframe_html(kind):
    return ("<!doctype html><html><head><meta charset=utf-8><title>frame %s</title></head>"
            "<body><p>frame %s</p><script>%s</script></body></html>") % (kind, kind, JFRAME_JS[kind])


def layout(ctx, name, title, marker, body, script="", park_until=None, park_keepalive_until=None):
    nav = (
        '<header><a id=logo href="%s">Daily Fake</a><nav>'
        '<a id=nav-category href="%s">Category</a>'
        '<a id=nav-search href="%s">Search</a>'
        '<a id=nav-signin href="%s">Sign in</a>'
        '<a id=nav-account href="%s">Account</a></nav></header>'
    ) % (link(ctx, "/"), link(ctx, "/category/world"), link(ctx, "/search"),
         link(ctx, "/signin"), link(ctx, "/account"))
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>%s</title><style>%s</style></head><body>"
        "%s<h1 id=marker>%s</h1>%s%s%s<p id=tail>end of %s</p></body></html>"
    ) % (html.escape(title), CSS, nav, html.escape(marker), body,
         ("<script>%s</script>" % script) if script else "", noise_script(ctx, park_until, park_keepalive_until), html.escape(name))


def teaser_li(ctx, i):
    return '<li><a class=teaser href="%s">%s</a></li>' % (link(ctx, "/article/%d" % i), html.escape(title_of(i)))


def page_home(ctx):
    items = "".join(teaser_li(ctx, i) for i in range(1, 6))
    body = (
        "<p>Top stories today.</p><ul id=teasers>%s</ul>"
        "<p><button id=loadmore type=button>Load more</button></p>"
    ) % items
    script = """
const list = document.getElementById('teasers');
document.getElementById('loadmore').addEventListener('click', async () => {
  const n = list.querySelectorAll('a.teaser').length;
  const r = await fetch('/api/more?from=' + n + %s);
  for (const it of await r.json()) {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.className = 'teaser'; a.href = it.href; a.textContent = it.title;
    li.appendChild(a); list.appendChild(li);
  }
});
""" % json.dumps("" if ctx.noise else "&noise=0")
    return layout(ctx, "home", "Daily Fake: home", "Home page", body, script)


def page_category(ctx, name, sort):
    idx = CATEGORIES.index(name)
    ids = [(idx * 8 + i) % N_ARTICLES + 1 for i in range(8)]
    orders = sort_orders(ids)
    items = "".join(
        '<li><a class=item href="%s">%s</a></li>' % (link(ctx, "/article/%d" % i), html.escape(title_of(i)))
        for i in orders[sort])
    body = (
        "<p>Sort: <button id=sort-new type=button>New</button> <button id=sort-top type=button>Top</button></p>"
        "<ul id=list data-sort=%s>%s</ul>"
    ) % (sort, items)
    script = """
const orders = %s, hrefs = %s, titles = %s;
const list = document.getElementById('list');
const render = (s) => {
  list.dataset.sort = s;
  list.textContent = '';
  for (const id of orders[s]) {
    const li = document.createElement('li'); const a = document.createElement('a');
    a.className = 'item'; a.href = hrefs[id]; a.textContent = titles[id];
    li.appendChild(a); list.appendChild(li);
  }
};
const choose = (s) => {
  const p = new URLSearchParams(location.search); p.set('sort', s);
  history.pushState({sort: s}, '', location.pathname + '?' + p.toString());
  render(s);
};
document.getElementById('sort-new').addEventListener('click', () => choose('new'));
document.getElementById('sort-top').addEventListener('click', () => choose('top'));
addEventListener('popstate', () => render(new URLSearchParams(location.search).get('sort') || 'default'));
""" % (json.dumps(orders),
       json.dumps({str(i): link(ctx, "/article/%d" % i) for i in ids}),
       json.dumps({str(i): title_of(i) for i in ids}))
    return layout(ctx, "category-" + name, "Daily Fake: " + name, "Category " + name, body, script,
                  park_until=PARK_PAGES["category"])


def page_article(ctx, i):
    paras = "".join(
        "<p>Paragraph %d of article %d. The quick brown fox jumps over the lazy dog while the market "
        "waits for the evening ferry, and the lanterns along the harbour are lit one by one until the "
        "whole waterfront glows. Nobody is in a hurry here, and the story takes its time.</p>" % (k, i)
        for k in range(1, 41))
    body = (
        '<img id=hero src="/img/%d.png" width=600 height=200 alt="hero image">'
        "<div id=text>%s</div>"
        '<p><a id=partner target=_blank href="%s">Partner site</a> | '
        '<a id=download href="%s">Download summary</a></p>'
        '<p><a id=next href="%s">Next article</a></p>'
    ) % (i, paras, ctx.other + link(ctx, "/partner/%d" % i), link(ctx, "/download/%d.txt" % i),
         link(ctx, "/article/%d" % (i % N_ARTICLES + 1)))
    return layout(ctx, "article-%d" % i, title_of(i), "Article %d" % i, "<h2>%s</h2>%s" % (html.escape(title_of(i)), body))


def search_ids(q):
    seed = sum(map(ord, q))
    return [(seed + 7 * k) % N_ARTICLES + 1 for k in range(3 + seed % 4)]


def page_search(ctx, q):
    hidden = "" if ctx.noise else '<input type=hidden name=noise value=0>'
    form = (
        '<form id=sform method=get action="/search">%s'
        '<input id=q name=q type=text value="%s" autocomplete=off> '
        '<button id=search-submit type=submit>Search</button></form>'
        '<p id=msg class=err hidden>Please type a search term.</p>'
    ) % (hidden, html.escape(q or "", quote=True))
    extra = ""
    marker = "Search"
    if q is not None and q.strip():
        marker = "Search results"
        results = "".join(
            '<li><a class=result href="%s">%s</a></li>' % (link(ctx, "/article/%d" % i), html.escape(title_of(i)))
            for i in search_ids(q))
        extra = '<h2>Results for: <span id=qshow>%s</span></h2><ul id=results>%s</ul>' % (html.escape(q), results)
    elif q is not None:
        extra = '<p id=msg-server class=err>Please type a search term.</p>'
    script = """
document.getElementById('sform').addEventListener('submit', (e) => {
  if (!document.getElementById('q').value.trim()) {
    e.preventDefault();
    document.getElementById('msg').hidden = false;
  }
});
"""
    return layout(ctx, "search", "Daily Fake: search", marker, form + extra, script,
                  park_keepalive_until=PARK_KEEPALIVE_PAGES["search"])


def page_signin(ctx, user="", error=None):
    err = '<p id=error class=err>%s</p>' % html.escape(error) if error else ""
    body = (
        '%s<form id=lform method=post action="%s">'
        '<p><label>User <input id=user name=user type=text value="%s" autocomplete=off></label></p>'
        '<p><label>Password <input id=pass name=password type=password autocomplete=off></label></p>'
        '<p><button id=login type=submit>Sign in</button></p></form>'
    ) % (err, link(ctx, "/signin"), html.escape(user, quote=True))
    return layout(ctx, "signin", "Daily Fake: sign in", "Sign in", body)


def page_account(ctx, user):
    body = (
        '<p>Signed in as <b id=user>%s</b>.</p>'
        '<form method=post action="%s"><button id=signout type=submit>Sign out</button></form>'
    ) % (html.escape(user), link(ctx, "/signout"))
    return layout(ctx, "account", "Daily Fake: account", "Account of " + user, body)


def page_partner(ctx, i):
    body = '<p>A page on the other origin.</p><p><a id=backlink href="%s">Back to the site</a></p>' % (
        ctx.other + link(ctx, "/"))
    return layout(ctx, "partner-%d" % i, "Partner %d" % i, "Partner %d (other origin)" % i, body)


class State:
    def __init__(self):
        self.origins = ["", ""]
        self.sessions = {}
        self.lock = threading.Lock()
        self.step = 0
        self.t0 = time.time()
        self.events = []
        self.cond = threading.Condition()
        self.released = set()

    def set_step(self, n):
        with self.cond:
            self.step = n
            self.cond.notify_all()

    def release(self, rid):
        with self.cond:
            self.released.add(rid)
            self.cond.notify_all()

    def hold(self, rid, until=0, failsafe=90.0):
        """Block until /release?id=rid arrives, or the walk reaches step `until`; the failsafe only
        keeps a lost event from blocking a thread forever."""
        end = time.time() + failsafe
        with self.cond:
            while rid not in self.released and not (until and self.step >= until) and time.time() < end:
                self.cond.wait(0.5)

    def record(self, idx, method, path, q):
        """Server-side log: what the server saw, tagged with the walk step that was current."""
        ident = (q.get("id") or q.get("t") or [""])[0]
        if path == "/beacon":
            cls = "beacon"
        elif path == "/r" and ident.startswith(("ka", "cak")):
            cls = "keepalive"
        elif path == "/r":
            cls = "resource"
        elif path.startswith("/jf/"):
            cls = "frame"
        else:
            cls = "page"
        with self.lock:
            self.events.append({"t": round(time.time() - self.t0, 3), "step": self.step, "origin": "AO"[idx],
                                "method": method, "path": path, "id": ident, "n": (q.get("n") or [""])[0], "src": (q.get("src") or [""])[0],
                                "class": cls})


class Site(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, addr, handler, idx, state):
        super().__init__(addr, handler)
        self.idx = idx
        self.state = state


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def handle(self):
        # A browser drops connections on purpose (aborted fetches, closed tabs); that is not an error.
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def _send(self, code, ctype, body=b"", headers=(), cache="no-cache"):
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _html(self, text, code=200, headers=()):
        self._send(code, "text/html; charset=utf-8", text.encode(), headers, cache=CACHE_PAGES)

    def _redirect(self, code, location, headers=()):
        self._send(code, None, b"", [("Location", location)] + list(headers))

    def _guard(self, fn):
        # Browsers abort requests on purpose; a dropped socket is not an error here.
        try:
            fn()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            self.close_connection = True

    def do_GET(self):
        self._guard(self._get)

    do_HEAD = do_GET

    def do_POST(self):
        self._guard(self._post)

    def do_OPTIONS(self):
        self._guard(lambda: self._send(204, None, b"", [
            ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"), ("Access-Control-Allow-Headers", "*")]))

    def _ctx(self, q):
        st = self.server.state
        idx = self.server.idx
        return SimpleNamespace(own=st.origins[idx], other=st.origins[1 - idx], noise=q.get("noise", ["1"])[0] != "0")

    def _session_user(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session":
                return self.server.state.sessions.get(v)
        return None

    def _post(self):
        u = urlsplit(self.path)
        self.server.state.record(self.server.idx, "POST", u.path, parse_qs(u.query, keep_blank_values=True))
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        q = parse_qs(u.query, keep_blank_values=True)
        if u.path == "/beacon":
            return self._send(204, None, b"", cache="no-store")
        form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        ctx = self._ctx(q)
        if u.path == "/signin":
            user = form.get("user", [""])[0]
            if user == USER and form.get("password", [""])[0] == PASSWORD:
                tok = secrets.token_hex(16)
                with self.server.state.lock:
                    self.server.state.sessions[tok] = user
                return self._redirect(303, link(ctx, "/account"), [
                    ("Set-Cookie", "session=%s; Path=/; HttpOnly; SameSite=Lax" % tok),
                    ("Set-Cookie", "theme=dark; Path=/; SameSite=Lax"),
                ])
            return self._html(page_signin(ctx, user, "Wrong user or password."))
        if u.path == "/signout":
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "session":
                    with self.server.state.lock:
                        self.server.state.sessions.pop(v, None)
            return self._redirect(303, link(ctx, "/"), [
                ("Set-Cookie", "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"),
                ("Set-Cookie", "theme=; Path=/; Max-Age=0; SameSite=Lax"),
            ])
        return self._send(404, "text/plain", b"not found")

    def _get(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query, keep_blank_values=True)
        p = u.path
        self.server.state.record(self.server.idx, self.command, p, q)
        ctx = self._ctx(q)
        if p == "/":
            return self._html(page_home(ctx))
        if p.startswith("/category/"):
            name = p[len("/category/"):]
            if name not in CATEGORIES:
                return self._send(404, "text/plain", b"unknown category")
            sort = q.get("sort", ["default"])[0]
            return self._html(page_category(ctx, name, sort if sort in ("new", "top") else "default"))
        if p.startswith("/article/") and p[9:].isdigit() and 1 <= int(p[9:]) <= N_ARTICLES:
            return self._html(page_article(ctx, int(p[9:])))
        if p.startswith("/partner/") and p[9:].isdigit():
            return self._html(page_partner(ctx, int(p[9:])))
        if p.startswith("/download/") and p.endswith(".txt"):
            num = p[10:-4]
            text = ("Summary of article %s.\nThe quick brown fox jumps over the lazy dog.\n" % num).encode()
            return self._send(200, "text/plain; charset=utf-8", text, [
                ("Content-Disposition", 'attachment; filename="summary-%s.txt"' % num)])
        if p == "/search":
            return self._html(page_search(ctx, q["q"][0] if "q" in q else None))
        if p == "/signin":
            return self._html(page_signin(ctx))
        if p == "/account":
            user = self._session_user()
            if user:
                return self._html(page_account(ctx, user))
            return self._redirect(302, link(ctx, "/signin"))
        if p == "/api/more":
            start_n = int(q.get("from", ["0"])[0] or 0)
            items = [{"title": title_of(i), "href": link(ctx, "/article/%d" % i)}
                     for i in range(start_n + 1, start_n + 4)]
            return self._send(200, "application/json", json.dumps(items).encode())
        if p.startswith("/img/"):
            return self._send(200, "image/png", sites.PNG_1X1)
        if p == "/beacon":
            return self._send(204, None, b"", cache="no-store")
        if p == "/r":
            return self._resource(q, ctx)
        if p == "/release":
            self.server.state.release(q.get("id", [""])[0])
            return self._send(200, "text/plain", b"ok", cache="no-store")
        if p == "/hold":
            until = int(q.get("until", ["0"])[0] or 0)
            self.server.state.hold(q.get("id", [""])[0], until)
            return self._send(200, "application/json", b'{"held":true}', cache="no-store")
        if p == "/stream":
            # headers and 10 of 20 body bytes at once; the rest after a short wait
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "20")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"0123456789")
            self.wfile.flush()
            self.server.state.hold("stream-" + q.get("id", [""])[0], 0, failsafe=3.0)
            self.wfile.write(b"0123456789")
            return
        if p.startswith("/jf/"):
            kind = p[4:]
            if kind not in JFRAME_JS:
                return self._send(404, "text/plain", b"unknown frame")
            return self._html(jframe_html(kind))
        return self._send(404, "text/plain", b"not found")

    def _resource(self, q, ctx):
        hops = int(q.get("redirect", ["0"])[0] or 0)
        if hops > 0:
            nq = {k: v[0] for k, v in q.items()}
            nq["redirect"] = str(hops - 1)
            return self._redirect(302, ctx.other + "/r?" + urlencode(nq))
        kind = q.get("kind", ["json"])[0]
        rid = q.get("id", [""])[0]
        nostore = "no-store"
        if kind == "js":
            return self._send(200, "application/javascript", ("/* %s */\n" % rid).encode(), cache=nostore)
        if kind == "css":
            return self._send(200, "text/css", b".fw-x{color:#222}\n", cache=nostore)
        if kind == "png":
            return self._send(200, "image/png", sites.PNG_1X1, cache=nostore)
        if kind == "svg":
            return self._send(200, "image/svg+xml", sites.SVG, cache=nostore)
        if kind == "woff2":
            return self._send(200, "font/woff2", sites.WOFF2, cache=nostore)
        if kind == "fontcss":
            font_url = self.server.state.origins[1] + "/r?kind=woff2&id=font"
            rules = "".join(
                "@font-face{font-family:FakeFont;font-weight:%d;src:url(%s) format('woff2');}\n" % (w, font_url)
                for w in (400, 500, 600, 700, 800))
            return self._send(200, "text/css", rules.encode(), cache=nostore)
        if kind == "json":
            return self._send(200, "application/json", json.dumps({"ok": True, "id": rid}).encode(), cache=nostore)
        return self._send(400, "text/plain", b"bad kind")


def start(ports=(8321, 8322), bind="127.0.0.1", public_host="127.0.0.1", other_host="localhost"):
    """Start both origins on daemon threads. Returns (origin_a, origin_o, stop)."""
    state = State()
    servers = [Site((bind, p), Handler, i, state) for i, p in enumerate(ports)]
    state.origins = ["http://%s:%d" % (host, s.server_address[1]) for host, s in zip((public_host, other_host), servers)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()

    def stop():
        for s in servers:
            s.shutdown()
            s.server_close()

    global STATE
    STATE = state
    return state.origins[0], state.origins[1], stop
