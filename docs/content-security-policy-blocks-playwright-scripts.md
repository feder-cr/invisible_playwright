---
title: "Content-Security-Policy Blocks Playwright's Injected Scripts"
description: "A page's CSP header can refuse addInitScript and evaluate the same way it refuses any other unauthorized script. bypassCSP fixes it and changes what the page actually enforces while you test it."
parent: "Testing and Troubleshooting"
grand_parent: "Guides"
nav_order: 23
---


# Content-Security-Policy Blocks Playwright's Injected Scripts

A page that ships a
[`Content-Security-Policy`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy)
header restricting `script-src` does not treat Playwright's injected code as special. A
`page.add_init_script()` payload, or code passed to `page.evaluate()` in certain
injection paths, is just another script the page did not explicitly authorize, and a
strict `script-src` blocks unauthorized inline and dynamically-injected script
execution the same way it blocks a stray `<script>` tag or a compromised third-party
widget. The failure is silent in the way CSP failures usually are: no exception in your
Python code, a console warning in the browser you likely never read, and code that
simply never ran.

That is worth reading as a property of the layer, not an accident. An init script is
page JavaScript, and page JavaScript is the outermost of
[the three levels anything can be changed at](playwright-stealth-levels.md): it is the
level the page itself is allowed to have an opinion about, and CSP is the page
exercising that opinion.

## What CSP actually restricts

By default, a policy that sets `default-src` or `script-src` blocks inline `<script>`
tags, inline event-handler attributes, `javascript:` URLs, and dynamic evaluation via
`eval()`, `Function()`, or a string passed to `setTimeout()`. A page can loosen this with
a `'nonce-<value>'` that has to match between the header and the script tag, or with
`'unsafe-inline'`, which defeats the point of having the policy at all and is
correspondingly rare on a page that bothered to set one.

None of these exceptions are things Playwright can produce on your behalf. Your injected
code did not ship with the page's nonce, because the nonce is generated server-side per
request specifically so a script that was not part of that response cannot forge it.

## Playwright's option, and what it actually does

`browser.new_context(bypass_csp=True)` (or the equivalent `bypassCSP` in JavaScript)
tells the browser context to skip CSP enforcement outright:

```python
context = browser.new_context(bypass_csp=True)
page = context.new_page()
page.goto("https://example.com")
# an init script that a strict CSP would otherwise have refused now runs
```

The option's own documented default is `False`, and what it does is exactly what the
name says: it toggles bypassing the page's Content-Security-Policy, entirely, for every
resource load in that context, not just for Playwright's own injections. It does not
repair a misconfigured policy, does not add your script's origin to an allowlist, and
does not tell you whether the same page would work for a real visitor whose browser
still enforces the header normally.

## The honest tradeoff

There are two different reasons you might reach for this flag, and only one of them
survives contact with what the flag actually does.

**Legitimate testing need: your own code, your own CSP.** If you are testing a page you
control and the point of the test is the page's functionality instead of its CSP
configuration, bypassing CSP to let your own instrumentation or a third-party testing
widget run is a reasonable, disclosed tradeoff. You know the header is there, you know
you are turning it off for the test, and the production page a real visitor loads still
enforces it normally. This is CSP getting in the way of your own test tooling, not CSP
doing its job against something adversarial.

**A deviation a real browser would never make.** If the target is not yours and the
question is "does my automated session look like a real visitor," bypassing CSP changes
what the page is allowed to do in ways a genuine visitor's browser never would. A real
person's browser enforces the header exactly as written; a session that quietly disables
it is not rendering the page the way the page's own operator configured it to render, and
that is a difference from a genuine session, not a neutral convenience. Whether a
specific target's own logic could ever observe that difference from inside the page is a
separate question this page cannot answer for you, but the premise underneath
"bypassing CSP is invisible" does not hold: it is a real change to how the page executes,
not a transparent workaround. It belongs on the same list as every other setting that
makes a session behave unlike a visitor's, which is the list
[the checklist for a session being detected](playwright-detected-as-bot.md) works
through in order.

The practical rule: use `bypassCSP` when you are testing your own integration against
your own header and you know exactly why it is there. Treat it as a real behavioral
deviation, not a free pass, on anything else.

## Diagnosing it before reaching for the flag

**Confirm CSP is actually the cause.** Open the browser console (or read
`page.on("console")` events in your script) and look for a message naming
`Content-Security-Policy` and the specific directive it refused. This distinguishes CSP
from a plain script error, which is a different bug with a different fix.

```python
page.on("console", lambda msg: print(msg.type, msg.text))
page.goto("https://example.com")
```

**Read the actual header**, not just the console message, to see which directives are
restrictive:

```python
response = page.goto("https://example.com")
print(response.headers.get("content-security-policy"))
```

**Check whether the restriction is `script-src` specifically**, versus something CSP also
covers but unrelated to your failure, like `connect-src` blocking an XHR your page tries
to make, or `frame-ancestors` unrelated to injected code entirely. Bypassing CSP for a
`script-src` problem also bypasses every other directive on the same header, which is
broader than most cases need.

**Known limitation:** documented reports exist of `bypassCSP` not resolving every CSP or
CORS error depending on the specific directive and browser version, so confirm the
console warning is actually gone after setting the option instead of assuming the flag
is unconditionally comprehensive.

## Diagnostic checklist

1. Check the browser console for a `Content-Security-Policy` message naming the refused
   directive before assuming your injection code is broken.
2. Read the response's `content-security-policy` header directly to see the actual
   policy instead of guessing from the symptom.
3. Confirm the refusal is `script-src` (or `default-src` covering it), not an unrelated
   directive that happens to also block something in your flow.
4. Decide whether this is your own page under test or a third-party target, and apply
   the tradeoff above instead of reaching for `bypass_csp` reflexively.
5. After setting `bypass_csp=True`, re-check the console to confirm the specific warning
   is gone; a version-specific gap means it is not always comprehensive.

## What invisible_playwright does and does not touch here

CSP enforcement is the browser's networking and script-execution security layer,
untouched by this project's fingerprint patching. `bypass_csp` is a standard Playwright
context option that behaves identically on a patched Firefox and a stock one; the engine
work here does not add, remove, or interact with CSP handling. The tradeoff above -
disclosed test convenience against a real behavioral deviation on someone else's page -
applies the same way regardless of which browser build enforces or bypasses the header.

## Conclusion

A CSP-restricted page refuses Playwright's injected scripts for the same reason it
refuses any other unauthorized code: that is precisely what the header is configured to
do. `bypass_csp=True` removes the restriction entirely rather than repairing it, which is
a fair trade when you are testing your own integration against your own policy and a real
deviation from how a genuine visitor's browser behaves everywhere else. Confirm CSP is
actually the cause before reaching for the flag, and know which of the two situations you
are actually in before you flip it.

## Short answers to the questions that lead here

**Why does my `addInitScript` silently not run?** A page's Content-Security-Policy header
can block unauthorized script execution, including injected init scripts, the same way it
blocks any other script it did not explicitly allow. Check the browser console for a CSP
message before assuming the injection failed for another reason.

**Does `bypassCSP` fix a broken CSP header?** No. It disables enforcement of the header
entirely for that context; it does not repair the policy, approve your script's origin,
or change what a real visitor's browser would do with the same page.

**Is bypassing CSP always safe to use for testing?** It is a reasonable, disclosed
tradeoff on your own page under your own policy. On a third-party target, it is a real
deviation from how a genuine browser renders the page, not a neutral workaround.

**Why doesn't a nonce fix my injected script?** Nonces are generated per-request and
embedded in the page's own script tags server-side; code Playwright injects afterward was
never part of that response and cannot carry a nonce it never received.

**Does bypassing CSP also disable CORS?** No. `bypassCSP` addresses Content-Security-Policy
enforcement specifically and does not disable cross-origin resource sharing checks, which
are a separate mechanism.

**Will bypassCSP always resolve the console warning?** Documented reports show it does not
always resolve every CSP or CORS error depending on the specific directive and browser
version, so confirm the warning is actually gone rather than assuming it is comprehensive.

## Sources

- Playwright's own API reference, [`browser.new_context()`](https://playwright.dev/python/docs/api/class-browser#browser-new-context-option-bypass-csp),
  for the exact `bypass_csp` option description and its `false` default.
- Mozilla Developer Network, [Content-Security-Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy),
  for what `script-src` restricts by default and how nonces and `'strict-dynamic'` work.
- Microsoft Playwright issue [#20078](https://github.com/microsoft/playwright/issues/20078),
  a report of CSP and `--disable-web-security` not behaving as expected in a specific
  configuration, evidence for the "known limitation" section above.
- Microsoft Playwright issue [#27696](https://github.com/microsoft/playwright/issues/27696),
  a feature request to set `bypassCSP` on an already-created context rather than only at
  context-creation time.

**See also:** [Playwright TargetClosedError: the causes and the fixes](playwright-targetclosederror-causes.md),
for another case where the page's own defenses are the actual variable rather than
anything wrong with the browser, and [How to test bot detection without a false pass](how-to-test-bot-detection.md),
for the broader discipline of treating a bypassed restriction as a documented deviation
rather than a free pass.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. CSP enforcement is the
browser's own security layer, unaffected by fingerprint patching in either direction.*
