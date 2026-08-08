---
title: "Network, Proxy and WebRTC"
description: "Everything that happens outside the JavaScript engine: proxy authentication and DNS, WebRTC candidates, timezone derivation, TLS fingerprinting, and what a container gives away over the network."
parent: "Guides"
has_children: true
nav_order: 3
---


# Network, Proxy and WebRTC

The layer below the page: the IP a site sees, the DNS queries behind it, the
candidates WebRTC exposes regardless of what the page's JavaScript does, the
timezone that has to agree with the exit node, and the TLS handshake that happens
before a single byte of HTML arrives. None of this is fixable from inside the page,
which is exactly why it is worth understanding on its own.

## Proxies, IP class and reputation

- [What is ASN and IP reputation in bot detection?](asn-and-ip-reputation-in-bot-detection.md) - How ASN and IP reputation score a network before any JavaScript runs.
- [Residential vs datacenter vs mobile proxies explained](residential-datacenter-mobile-proxies-explained.md) - What each proxy class looks like to a site: ASN owner, reverse DNS, shared exits.
- [What does a mobile carrier IP look like to a site?](what-a-mobile-carrier-ip-looks-like.md) - Operator ASN, carrier-grade NAT sharing and churn, paired with a desktop fingerprint.
- [Sticky vs rotating proxy sessions: which to use](sticky-vs-rotating-proxy-sessions.md) - How each maps to one launch and one exit, and what neither fixes.
- [Does chaining two proxies help avoid detection?](does-chaining-two-proxies-help-detection.md) - Chaining hides your origin from the first operator, but the target scores only the last exit.
- [Playwright proxy per context: what it does not isolate](playwright-proxy-per-context.md) - Rotates the exit IP but not the fingerprint: canvas, GPU, fonts and audio stay identical.
- [Web scraping keeps getting blocked with good proxies](web-scraping-getting-blocked-proxies.md) - A residential proxy fixes the IP, not the machine, when country disagrees with timezone or language.
- [Playwright in Docker: it runs, and still gets blocked](playwright-docker-detection.md) - The container describes a datacenter machine: no GPU, few fonts, no audio, a default screen.

## Proxy protocols, DNS and IP leaks

- [SOCKS5 vs HTTP proxy: what each does in the browser](socks5-vs-http-proxy-browser.md) - Who authenticates and where: SOCKS auth and DNS in the engine, HTTP auth in the driver.
- [Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md) - Why credentials on a socks5:// server fail silently, and the routes that work.
- [Does a proxy leak DNS? DoH and DNS leaks explained](does-a-proxy-leak-dns-doh-explained.md) - How a SOCKS5 proxy leaks DNS when the host resolves names locally, and how to verify.
- [IPv6 vs IPv4: which does your proxy expose?](ipv6-vs-ipv4-which-does-your-proxy-use.md) - On a dual-stack host the browser can reach a site over IPv6 while the proxy carries only IPv4.
- [How to check if a proxy leaks your real IP](how-to-check-proxy-ip-leak.md) - Confirm the actual WebRTC, IPv6, DNS and timezone values, not just that a leak is absent.

## WebRTC candidates and leaks

- [WebRTC leak with a proxy in Playwright and Selenium](webrtc-leak-proxy.md) - A SOCKS5 proxy does not stop a leak, and disabling WebRTC trades it for a detectable signature.
- [WebRTC IP that matches the proxy exit, by design](webrtc-ip-match-proxy-exit.md) - The server-reflexive candidate equals the exit IP, with the real NAT port preserved.
- [WebRTC IPv6 leak: why a proxy does not stop it](webrtc-ipv6-leak-proxy.md) - A SOCKS proxy carries only TCP, so an IPv6 host still emits its real global address.
- [WebRTC has no ICE candidates behind a proxy](webrtc-no-candidates-behind-proxy.md) - An empty candidate list reads as tampering: why residential proxies drop the UDP STUN needs.
- [WebRTC ICE candidate spoofing: the fields that give it away](webrtc-ice-candidate-spoofing.md) - Priority, foundation and arrival time are all checkable, not just the address.
- [about:webrtc: read your real ICE candidates](about-webrtc-debug-ice-candidates.md) - Tell a real LAN-IP leak from a masked .local host and a proxy-egress srflx line.

## TLS and HTTP fingerprint

- [JA3 and JA4: why a TLS fingerprint cannot be patched](ja3-ja4-tls-fingerprint.md) - Set by the TLS library before your code runs, so no stealth plugin can patch them.
- [TLS fingerprint vs User-Agent: the contradiction](tls-fingerprint-user-agent-mismatch.md) - The handshake is evidence the engine produced; the header is only a claim.
- [HTTP/2 fingerprint: the layer above the TLS handshake](http2-fingerprint-detection.md) - The SETTINGS frame, window update and pseudo-header order form a fingerprint unreachable from JavaScript.
- [HTTP/3 and QUIC fingerprint: what a site sees](http3-quic-fingerprint-what-a-site-sees.md) - QUIC exposes an ordered set of transport parameters distinct from JA3/JA4 and HTTP/2.
- [Why a Python requests scraper is blocked: TLS fingerprint](web-scraping-tls-fingerprint-requests-blocked.md) - A requests scraper has its own TLS fingerprint, blocked at the handshake before any header.

## Geo and timezone consistency

- [Geolocation API vs IP location: keep them consistent](geolocation-api-vs-ip-location-consistency.md) - Coordinates, browser timezone and exit IP country are three signals a site cross-checks.
- [Playwright timezone does not match the proxy IP](timezone-proxy-mismatch.md) - Timezone and locale are several surfaces a detector cross-checks against your exit IP.
- [Offline timezone resolution from a proxy exit IP](offline-geoip-timezone-proxy.md) - Resolve the browser timezone offline from a self-updating local database, no per-launch API call.
