---
title: "Execution context was destroyed, and when it means detection"
description: "Execution context was destroyed is usually a race condition, and sometimes a site deciding mid-visit it does not like you. The two look identical in the stack trace and need entirely different fixes."
parent: "The Automation Layer"
grand_parent: "Guides"
nav_order: 4
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://feder-cr.github.io/invisible_playwright/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Guides",
      "item": "https://feder-cr.github.io/invisible_playwright/guides.html"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "The Automation Layer",
      "item": "https://feder-cr.github.io/invisible_playwright/guides-automation-layer.html"
    },
    {
      "@type": "ListItem",
      "position": 4,
      "name": "Execution context was destroyed, and when it means detection"
    }
  ]
}
</script>

# Execution context was destroyed, and when it means detection

```
Error: Execution context was destroyed, most likely because of a navigation
```

This is one of the most searched Playwright and Puppeteer errors, and almost every
answer to it is about race conditions, which is correct most of the time. It is also
the error you get when a site decides mid-visit that it does not like you, and the two
cases look identical from the stack trace.

This page separates them, because the fixes have nothing in common and people spend
days applying one to the other.

## What the error actually is

When you evaluate JavaScript in a page, the browser runs it inside an execution context
belonging to that document. Navigate, and that document is gone, and so is its context.
Anything still holding a reference to it fails.

```python
page.goto("https://example.com")
handle = page.query_selector("#thing")
page.click("#submit")          # triggers a navigation
handle.inner_text()            # the context this handle belonged to no longer exists
```

The error is accurate and the browser is behaving correctly. Something navigated while
you were still talking to the old page.

Related messages with the same root cause: `Target closed`, `Frame was detached`,
`Node is detached from document`.

## The ordinary causes, which are most of them

**A handle that outlived its page.** Element handles, frames and contexts are bound to
a document. Re-query after any navigation instead of carrying references across one.

**An implicit navigation you did not plan.** A click on a link or a submit button
navigates. So does a form submission, a meta refresh, and a framework doing
client-side routing that replaces the document.

**Racing the load.** Evaluating while the page is still settling, so your call lands
just as a redirect fires.

**A redirect chain.** `goto` returns on the first response; a page that then bounces
through two more URLs destroys contexts under you.

The fixes are the usual ones: wait for the navigation instead of guessing, re-query
handles after it, and use the API's own waiting rather than sleeping.

```python
with page.expect_navigation():
    page.click("#submit")
page.query_selector("#thing").inner_text()      # fresh handle, new context
```

If that solves it, you are done, and the rest of this page is not about you.

## When it is the site, not your code

Now the case nobody writes about.

A site that decides a session is automated does not usually return a clean error. It
redirects: to a challenge page, to an interstitial, to a login wall, sometimes to the
same URL with different content. Every one of those is a navigation, and a navigation
mid-evaluation produces exactly this error.

So the same message means either "my code raced" or "the page moved me somewhere else
because it did not want me".

Four things separate them:

**It happens on one site and never elsewhere.** Race conditions are a property of your
code and show up wherever the timing is tight. A block is a property of one target.

**It happens at the same point in the flow every time.** Races are intermittent by
nature. A challenge fires reliably, usually after the same action.

**The final URL is not the one you asked for.** Read it when the error fires. This is
the single most useful thing you can do, and it takes one line.

**It arrives after an interaction rather than at load.** A navigation triggered by a
scoring decision usually comes after something happened, which is also the signature of
behavioural analysis.

## The four lines that tell you which one you have

Catch the error and look at the page instead of the stack:

```python
try:
    ...
except Exception:
    print("url:", page.url)
    print("title:", page.title())
    print("body head:", page.content()[:400])
    page.screenshot(path="where-did-i-end-up.png")
```

Then read the screenshot. Not the log, the image.

- The URL you expected, and the content you expected: your code raced.
- A different URL, a challenge, an interstitial, or a body far shorter than the real
  page: the site moved you, and the error is a symptom.

That distinction takes a minute and it decides whether you spend the next day on
waiting logic or on the browser's fingerprint.

## If it turns out to be the site

Then the error is not the problem and fixing your waits will not help. Work
[the checklist](playwright-detected-as-bot.md) in order, and note two things it will
tell you that are relevant here specifically:

- A block that arrives **after an interaction** points at behaviour rather than at the
  fingerprint. That is step five, and
  [the pointer events page](human-mouse-movement.md) is the detail.
- A block that arrives **at the first load** points at the fingerprint or the address,
  which is steps three and seven.

The timing of the navigation is itself evidence, and it is free.

## Short answers to the questions that lead here

**How do I fix "Execution context was destroyed"?** If it is a race, wait for the
navigation and re-query your handles afterwards. If the page navigated because the site
sent you somewhere, no amount of waiting fixes it.

**Is this error a sign of bot detection?** Sometimes. It is a sign that a navigation
happened during your evaluation, and a redirect to a challenge is a navigation.

**How do I tell the difference?** Read `page.url` and take a screenshot when it fires.
If you ended up somewhere you did not ask for, it is the site.

**Does `wait_until="networkidle"` fix it?** It changes when `goto` returns, which helps
with some races and does nothing for a redirect that happens later.

**Why does it only happen in headless or only in CI?** Usually timing, because a loaded
machine changes the race. Occasionally the environment, because a container is more
detectable than your laptop and a challenge fires there and not at home.

**What about "Target closed"?** Same family. The page or the browser went away while you
were talking to it, and the diagnosis is the same: find out what the browser was doing
at that moment.

**See also:** [the checklist for being detected on one site](playwright-detected-as-bot.md),
which is where to go if the URL came back wrong, and
[Playwright in Docker](playwright-docker-detection.md), for the case where it only
happens in CI.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright).
The screenshot-on-failure habit is the cheapest debugging change in this whole subject
and the one most often skipped.*
