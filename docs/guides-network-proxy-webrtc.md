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
