---
title: "Client Hints and Sec-Fetch: headers that must agree"
description: "Client Hints (Sec-CH-UA) and Sec-Fetch headers come from the browser's own state, so they are cheap to compare and hard to fake. Why Firefox sits differently."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 6
---


# Client Hints and Sec-Fetch: headers that must agree

[Client Hints](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Client_hints) and
Sec-Fetch are two families of request headers the browser generates from
its own internal state, not from the page and not from anything you configure. That makes
them awkward to fake and very cheap to check, which is a combination detectors like.

They also catch a specific mistake: changing the user agent string and nothing else.

This page is what each family carries, the mismatches that get caught, why Firefox and
Chromium are in structurally different positions here, what automation can and cannot
control, and how to check your own.

## Client Hints: one identity in three places

Modern Chromium reports its identity in three places that come from the same internal
state:

```
Sec-CH-UA: "Chromium";v="141", "Not?A_Brand";v="8", "Google Chrome";v="141"
Sec-CH-UA-Mobile: ?0
Sec-CH-UA-Platform: "Windows"
```

```js
navigator.userAgentData.brands       // the same brands and versions
navigator.userAgentData.platform     // the same platform
await navigator.userAgentData.getHighEntropyValues(['platformVersion', 'architecture'])
```

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/141.0.0.0 ...
```

In a real browser those three agree by construction, because one piece of state produces
all of them. Comparing them is a few lines on the server and it needs no fingerprinting
library at all.

**The mismatches that get caught:**

- **A spoofed user agent with untouched Client Hints.** You set the string to Chrome 141
  and the headers still describe whatever the browser really is. This is the single most
  common version of the mistake, because changing the string is the thing every guide
  tells you to do.
- **A version that differs between the two.** A current version in the user agent and an
  older one in the hints is a straight contradiction.
- **A platform that differs.** Claiming Windows in the string while the hint reports the
  host operating system.
- **The JavaScript API disagreeing with the headers.** Patching
  [`navigator.userAgentData`](https://developer.mozilla.org/en-US/docs/Web/API/NavigatorUAData)
  in the page does nothing to the headers, which were sent before your script existed.

That last one is worth sitting with. The headers travel with the request. A page-level
override cannot reach them, ever, because they were already sent.

## Firefox is in a different position, in both directions

Firefox does not implement User Agent Client Hints. It sends no `Sec-CH-UA` headers and
has no `navigator.userAgentData`.

That cuts two ways and both are worth being explicit about.

**In your favour, if you are actually Firefox.** There is no second identity pipeline to
keep synchronised. The absence is correct, expected, and matches every other real Firefox
on the internet. The whole class of mismatch above simply does not apply.

**Against you, if you claim to be Chrome.** A request whose user agent says Chrome and
which carries no Client Hints at all is contradicted by its own headers. There is no way
to fix that from inside a Firefox, because you would have to implement the pipeline.

So "which browser should I claim" is not a free choice. Claim the one you are running.
[Which is the argument for not setting the user agent at all](playwright-user-agent.md).

## Sec-Fetch: how the request was initiated

[Sec-Fetch](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Sec-Fetch-Site)
is a separate family of headers, present in every modern browser including
Firefox, that describes how a request was initiated rather than what the browser is. It is
much less discussed than Client Hints, and just as cheap for a server to check.

```
Sec-Fetch-Site: same-origin | same-site | cross-site | none
Sec-Fetch-Mode: navigate | cors | no-cors | same-origin
Sec-Fetch-Dest: document | image | script | style | empty
Sec-Fetch-User: ?1        (only present when a user activated the navigation)
```

These describe **the circumstances of the request**, not the browser. The browser fills
them in from what it knows: where the request came from, what it is for, and whether a
user gesture triggered it.

Why that is interesting for automation:

- A top-level navigation typed into the address bar carries `Sec-Fetch-Site: none`. One
  that came from clicking a link on the same site carries `same-origin`. A request
  fabricated outside the normal flow can easily carry the wrong one.
- `Sec-Fetch-User: ?1` appears only when the navigation was user activated. Automation
  that clicks properly produces it; automation that navigates programmatically does not,
  and a page that expected a click can notice the difference.
- `Sec-Fetch-Dest` has to match what the request is actually for. A document request that
  claims to be an image is not a browser.

The connection to the rest of this subject: user activation is also readable in the page,
through `navigator.userActivation`, and it is the value that
[an automation driver can announce by accident](debugger-timing-detection.md). Getting
that wrong in either direction, claiming a gesture nobody made or lacking one a real flow
would have, is the same class of inconsistency as everything else here.

## What automation can and cannot control

The practical division:

**Cannot be set from the page.** All of it. These headers are constructed by the network
stack when the request is made. Anything you do in JavaScript happens after.

**Can be set by the automation layer**, badly. Most frameworks let you add or override
request headers. Overriding these by hand is how you produce a set that no real browser
sends, because you are now maintaining by hand something the browser was maintaining
correctly.

**Should be left alone.** That is the whole recommendation. If the browser is real and
you have not overridden its identity, both families are correct for free.

The one legitimate case is adding headers a site requires for its own API, which is
unrelated to identity and does not touch either family.

## Checking your own

Send a request to any service that echoes headers back and read the full set, then
compare against what the same page reports in JavaScript.

```js
// in the page
console.log(navigator.userAgent);
console.log(navigator.userAgentData);      // undefined in Firefox, and that is correct
if (navigator.userAgentData) {
  console.log(await navigator.userAgentData.getHighEntropyValues(
    ['platformVersion', 'architecture', 'model', 'uaFullVersion']));
}
```

What you want:

- If `navigator.userAgentData` exists, its brands, version and platform match both the
  user agent string and the `Sec-CH-UA` headers on the request.
- If it does not exist, no `Sec-CH-UA` headers are being sent either, and the user agent
  claims a browser that genuinely does not send them.
- `Sec-Fetch-Site` matches how you actually navigated.
- `Sec-Fetch-User` is present when you clicked and absent when you navigated
  programmatically.
- Header **order** matches a real browser's, [a separate surface](http2-fingerprint-detection.md)
  one layer down, and one more reason not to hand-assemble any of this.

## Conclusion

These headers are hard to fake for a good reason: the browser generates them from state
it already has, so a real browser is consistent without trying and a spoofed one has to
maintain three copies of an identity by hand.

The practical advice is short. Do not override the user agent, so there is nothing to
keep in sync. Do not hand-set these headers. Claim the browser you are actually running,
because Firefox not sending Client Hints is correct for Firefox and damning for something
claiming to be Chrome.

## Short answers to the questions that lead here

**What is `Sec-CH-UA`?** A request header carrying the browser's brand and version,
generated from the same internal state as the user agent string.

**Why do Client Hints give away a spoofed user agent?** Because changing the string does
not change the headers, so the two describe different browsers.

**Does Firefox send Client Hints?** No, and it has no `navigator.userAgentData`. That is
correct for Firefox and a contradiction for anything claiming to be Chrome.

**Can I set Client Hints from JavaScript?** No. The headers are sent before your script
runs.

**What is `Sec-Fetch-User`?** A header present only when a user gesture triggered the
navigation.

**Should I override these headers in my automation?** No. A real browser fills them in
correctly, and hand-maintaining them is how you produce a combination that does not
exist.

## Sources

- WICG, [User-Agent Client Hints](https://wicg.github.io/ua-client-hints/), retrieved
  2026-08-28, for the `Sec-CH-UA` headers and `navigator.userAgentData`, as implemented in
  Chromium and absent in Firefox.
- W3C, [Fetch Metadata Request Headers](https://www.w3.org/TR/fetch-metadata/), retrieved
  2026-08-28, for `Sec-Fetch-Site`, `-Mode`, `-Dest` and `-User`.
- This project's handling of user activation, which is described on
  [the debugger page](debugger-timing-detection.md).

**See also:** [why you should not set the user agent](playwright-user-agent.md), which is
the same argument from the other end,
[JA3 and JA4](ja3-ja4-tls-fingerprint.md), for the layer below these headers, and
[how to test whether your browser is detected](how-to-test-bot-detection.md).

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level. Running a real Firefox means the Client Hints
question answers itself, which is the kind of advantage you only get by not lying in the
first place.*
