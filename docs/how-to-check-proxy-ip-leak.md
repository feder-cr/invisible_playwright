---
title: "How to check if a proxy leaks your real IP"
description: "Check if a proxy leaks your real IP in Playwright by confirming the actual WebRTC, IPv6, DNS and timezone values, not just that a leak is absent."
parent: "Network, Proxy and WebRTC"
grand_parent: "Guides"
nav_order: 16
---


# How to check if a proxy leaks your real IP

The reliable proxy-leak check confirms the correct WebRTC, IPv6, DNS and timezone values
are present for the proxy's exit, not that the wrong ones are absent. Most checks do the
opposite: they are written as a list of things that must not appear - the real LAN
address must not show up, no IPv6 candidate, no DNS query outside the tunnel - and every
one of those assertions is also true of a browser that leaks nothing because it does
nothing.

That is the trap this page is about. A "no leak" result can mean the surface you were
testing is dead, and a dead surface is a worse tell than a leaky one, because no real
browser has it. Below is what to assert, where to run it, and runnable code.

## The check most people run, and why it can pass a dead browser

The usual WebRTC leak check asks two questions. Does a candidate contain my LAN IP? Does
an IPv6 address appear? If both answers are no, it prints "no leak" and stops.

We shipped exactly that gate, and it cost us. It asserted the sensible negatives, the
host candidate does not expose the LAN address and zero IPv6 candidates appear, and it
passed run after run. Then someone opened the actual page behind a proxy and WebRTC was
returning nothing at all. Gathering was blocked, no candidates of any kind, and the gate
was green because a dead feature leaks nothing. Every negative assertion is satisfied by
a surface that never runs.

The failure generalises past WebRTC. "No real IP in the DNS log" is satisfied by DNS that
never resolved. "Timezone does not disagree with the exit" is satisfied by a timezone API
that returned undefined. A checklist of absences cannot tell working from broken, which is
the one distinction that matters here.

## Positive form: confirm the values, do not just check they are gone

The fix is a rule you can apply to every surface: assert the presence of the right signal,
not the absence of a wrong one. A result that comes back empty, blocked, or still loading
is a failure, not a pass.

For WebRTC behind a proxy, the positive form has three parts, and all three must be
present for the check to pass:

- **A host candidate** whose address is a `<uuid>.local` mDNS name. A real Firefox
  obfuscates the LAN address this way by default. A raw `192.168.x.x` here is a leak; an
  absent host candidate is a broken browser.
- **A server-reflexive candidate** (`typ srflx`) whose address equals the proxy egress IP.
  This is what a real machine behind NAT produces, and its address is what a remote peer
  would see.
- **The gathering completes.** The page finishes, not "Computing" forever.

A patched engine driven through stock Playwright produces this shape on its own: the host
candidate is masked to `<uuid>.local`, and the server-reflexive candidate carries the
proxy exit address, matching a real browser behind NAT rather than a suppressed one. Your
job in a leak check is to confirm those values, not to hope for silence. For the full
account of why the standard "just disable WebRTC" advice fails this bar, see
[WebRTC leak with a proxy](webrtc-leak-proxy.md).

## Run it through the proxy, on a real page, not localhost

Two ways a leak check can be true and prove nothing.

**Localhost bypasses the proxy.** A page served from `127.0.0.1` is not routed through the
tunnel, so you measured the path production does not use. The egress you confirm has to be
the one your job actually exits through, which means loading a real remote page through the
same proxy configuration.

**A curl-level test is a different client.** Checking the exit IP with a command-line tool
tells you the tool's exit, not the browser's. Confirm the address from inside the browser,
on the same page, through the same proxy, because that is the client a site sees.

The general rule is to feed the check the same inputs production gets: the real proxy, a
real page, the browser you actually ship. Anything less is a test of something you do not
run.

## The four surfaces to confirm: WebRTC, IPv6, DNS, timezone

A real IP escapes through more than one channel, and each wants its own positive check.
At a glance, this is the value to confirm on each surface and the false "no leak" that
each one hides:

| Surface | Positive value to confirm | The false "no leak" it hides |
|---|---|---|
| WebRTC | Host masked to `.local`, server-reflexive equal to the egress, gathering complete | WebRTC gathered nothing at all |
| IPv6 | No global IPv6 candidate, while the v4 server-reflexive one still appears | Gathering never ran, so no v6 could appear |
| DNS | Resolution happens at the proxy exit, not your local resolver | DNS never resolved |
| Timezone and locale | Browser clock matches the exit country | Timezone API returned undefined |

- **WebRTC** is the loud one, covered above: host masked to `.local`, server-reflexive
  equal to the egress, gathering complete.
- **IPv6** leaks past a v4-only proxy when the host has native IPv6 and a candidate carries
  a global v6 address. The correct state here is that no global IPv6 candidate appears while
  the v4 server-reflexive one still does. That is an absence you can only trust because the
  v4 candidate proves gathering ran.
- **DNS** should resolve through the proxy, not your local resolver. With a SOCKS proxy
  this is the [remote-DNS behaviour](does-a-proxy-leak-dns-doh-explained.md); confirm
  resolution happens at the exit rather than leaking your ISP's resolver. See
  [SOCKS5 versus HTTP proxy](socks5-vs-http-proxy-browser.md) for which schemes carry DNS
  through the tunnel.
- **Timezone and locale** are not an IP but they undo one. An exit in one country with a
  browser clock in another is a mismatch a detector cross-checks directly.
  [When the timezone does not match the proxy](timezone-proxy-mismatch.md) lists every
  surface that has to agree, and it is more than one value.

The header surface belongs on the same list, since request headers also have to line up
with the claimed platform: see [client hints and Sec-Fetch](client-hints-sec-fetch.md).

## A runnable check with invisible_playwright

Switching from stock Playwright is the launch line; every method after it is standard
Playwright. Install it first:

```bash
pip install invisible-playwright
```

Then gather ICE candidates through the proxy and assert the positive form. The `seed`
makes the run reproducible, so a failure can be replayed rather than guessed at:

```python
from invisible_playwright import InvisiblePlaywright

PROXY = {"server": "socks5://gate.example.com:1080", "username": "u", "password": "p"}

with InvisiblePlaywright(seed=42, proxy=PROXY) as browser:
    page = browser.new_page()
    page.goto("https://example.com")   # a real remote page, loaded through the proxy

    candidates = page.evaluate("""
        async () => {
            const pc = new RTCPeerConnection({
                iceServers: [{urls: 'stun:stun.l.google.com:19302'}]
            });
            pc.createDataChannel('probe');
            const lines = [];
            pc.onicecandidate = e => { if (e.candidate) lines.push(e.candidate.candidate); };
            await pc.setLocalDescription(await pc.createOffer());
            await new Promise(r => setTimeout(r, 4000));   // let gathering finish
            pc.close();
            return lines;
        }
    """)

    host  = [c for c in candidates if "typ host"  in c]
    srflx = [c for c in candidates if "typ srflx" in c]

    # POSITIVE form: gathering must COMPLETE and produce both kinds.
    # An empty list here is a FAIL, not a clean pass.
    assert host,  "FAIL: no host candidate - WebRTC produced nothing"
    assert srflx, "FAIL: no server-reflexive candidate - gathering blocked"

    print("host :", host[0])
    print("srflx:", srflx[0])
```

Now read the addresses out of the candidate strings and check them against the values they
should hold. A WebRTC candidate is a space-separated line, and the address is the fifth
field:

```python
    def address(candidate):
        # "candidate:<foundation> <comp> udp <prio> <ADDRESS> <port> typ ..."
        return candidate.split()[4]

    host_addr  = address(host[0])
    srflx_addr = address(srflx[0])

    # Fetch the exit IP through the SAME proxy, from inside the browser.
    egress = page.evaluate(
        "() => fetch('https://example.com/ip').then(r => r.text())"  # your own IP-echo endpoint
    ).strip()

    assert host_addr.endswith(".local"), f"FAIL: host is a raw LAN IP: {host_addr}"
    assert srflx_addr == egress,         f"FAIL: srflx {srflx_addr} != egress {egress}"

    # No global IPv6 candidate should appear, but only trust that because
    # the v4 server-reflexive one above proves gathering actually ran.
    assert not any(":" in address(c) for c in candidates if "typ srflx" in c), \
        "FAIL: a global IPv6 candidate leaked past the proxy"

    print("egress confirmed inside the browser:", egress)
```

Finally, confirm the browser clock follows the exit rather than your own machine:

```python
    tz = page.evaluate("() => Intl.DateTimeFormat().resolvedOptions().timeZone")
    print("browser timezone:", tz)   # must match the exit country, not your host
```

Every one of these asserts a value that must be present and correct. None of them passes on
a browser that returned nothing, which is the whole point. Run the whole thing at least ten
times and open a screenshot of the leak page as well as reading the log, because a text log
shows what your code extracted and a screenshot shows what the page actually rendered.

## Conclusion

A proxy leak check is only as good as what it asserts. Written as a list of absences it
will, sooner or later, hand a clean bill to a browser whose WebRTC, DNS or timezone is
simply dead, and a dead surface is a stronger tell than a leaky one because no real browser
has it. Confirm the real values instead: a `.local` host candidate, a server-reflexive
address equal to the exit, DNS through the tunnel, a clock that matches the country. Run it
through the proxy on a real page, repeat it, and read the screenshot. Do that and "no leak"
starts meaning what you wanted it to mean.

## Short answers to the questions that lead here

**How do I check if my proxy leaks my real IP?** Load a real page through the proxy, gather
WebRTC candidates in the browser, and assert the host candidate is a `.local` name and the
server-reflexive address equals your exit IP. Confirming the correct values beats checking
that the wrong ones are absent.

**My leak test says no leak. Am I safe?** Not necessarily. "No leak" is also what a dead
WebRTC, a failed DNS lookup, or an undefined timezone produces. Check that the surface
actually returned the right value, not just that it returned nothing bad.

**Should I just disable WebRTC to stop the leak?** No. A browser with WebRTC switched off is
a rare and detectable state. Real browsers gather candidates; the fix is to mask the LAN
address and route the reflexive candidate through the proxy, not to kill the feature.

**Can I test the leak on localhost?** No. Localhost bypasses the proxy, so you measured the
path you do not deploy. Load a remote page through the same proxy the job uses and confirm
the egress from inside the browser.

**Why does the WebRTC IP differ from my proxy IP?** If the server-reflexive candidate does
not equal your exit, either the proxy is not carrying the media path or the browser is
gathering on a real interface. If it is empty instead, gathering was blocked, which is a
separate failure.

**Does an IPv6 address leak through an IPv4 proxy?** It can, when the host has native IPv6
and the proxy only carries v4. Confirm no global IPv6 candidate appears while the v4
reflexive candidate still does, so you know gathering ran.

**See also:** [WebRTC leak with a proxy](webrtc-leak-proxy.md) for why the standard fixes
fail, [when the timezone does not match the proxy](timezone-proxy-mismatch.md) for the
surfaces that must agree with the exit, and
[how to test whether your browser is detected](how-to-test-bot-detection.md) for the
compare-against-a-real-browser method these checks sit inside.

## Sources

- This project's release gates, including the WebRTC gate whose negative-only assertions
  passed a fully blocked WebRTC behind a proxy, and the positive-form check that replaced
  it: host candidate as a `.local` name, server-reflexive address equal to the proxy exit,
  gathering completing.
- The public leak and fingerprint pages read from their own output, including BrowserLeaks
  for per-surface values and CreepJS for how a suppressed signal is recorded rather than
  ignored.
- Playwright's documented [proxy configuration](https://playwright.dev/python/docs/network)
  and `page.evaluate`, used unchanged.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The negative-only WebRTC gate
described here is a mistake this project made and then fixed, which is why the rule is
written in the positive form.*
