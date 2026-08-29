---
title: "fake-useragent is archived: what changes and what doesn't"
description: "The fake-useragent Python package was archived by its maintainer in 2026. What breaks, what to use instead, and why the string alone was never the whole fix."
parent: "Browser Identity"
grand_parent: "Guides"
nav_order: 16
---


# fake-useragent is archived: what changes and what doesn't

The `fake-useragent` Python package was archived by its own maintainer in 2026.
Archived means read-only: no new releases, no merged fixes, and a bundled string
database frozen at whatever it held on the archive date. If a script that used to
pull a random, realistic-looking user agent string suddenly started failing on
import, that is why. GitHub's archive state is unambiguous: no issue or pull request
against the repository will ever be merged again.

The practical break is real, but the premise underneath the package was always
incomplete: a current user agent string is only one claim a session makes, and
detectors check whether the claims agree with each other, not whether any single
string looks plausible on its own. This page covers what breaks, what to replace it
with, and why [changing the user agent string alone was never enough](is-changing-user-agent-enough.md).

## What actually breaks

`fake-useragent` did one specific job well: keep a database of real, current user
agent strings scraped from real traffic, and hand back a random one so a script's
requests didn't all announce themselves with the same static, obviously-scripted
string. That database is now frozen at whatever it contained on the archive date. It
will not pick up new Chrome or Firefox version strings as they ship, and eventually
every string it hands out will be for a version old enough to be its own tell.

The immediate practical break, for anyone who imported it expecting continued
service: no new releases, no fixes for the inevitable moment the data source it
scraped changes shape, and an import that plenty of tutorials still show as the
default way to get a "realistic" header.

## The part worth understanding regardless of the archive date

Losing an actively updated string database is a real, practical loss. But the
premise underneath it - that a realistic, current user agent string is what makes a
request look legitimate - was already incomplete before this package existed and
stays incomplete with a perfectly current one.

[A user agent string is one claim among many a real session makes](playwright-user-agent.md),
and a request or a browser session is checked for whether those claims agree with
each other, not for whether any single one of them looks plausible in isolation. A
freshly scraped, perfectly current Chrome 130 user agent string sitting on top of
[a TLS handshake that names a different browser](tls-fingerprint-user-agent-mismatch.md),
or a font list that doesn't match the claimed OS, is a contradiction - it just takes one more field to notice than a
stale or obviously-fake string does. The database going stale makes the tell
cheaper to find. It doesn't create a problem that a live, always-current database
would have solved outright.

## What to check in your own setup

If a script depended on this package for HTTP requests without a real browser
attached, the honest fix is deciding what's actually being verified against you: a
static list of current strings, hand-maintained and updated on your own schedule,
covers the same ground for [a pure HTTP client](http-client-vs-real-browser.md) and
doesn't depend on an external package's continued maintenance either way.

If the user agent is coming from an actual browser session - Playwright, Selenium,
anything driving a real engine - the better fix is not to set it at all. The
browser's own, honest string already matches everything else the browser reports,
which a hand-picked or randomly-sampled string from any database, maintained or
archived, cannot guarantee on its own.

## Short answers to the questions that lead here

**Is fake-useragent still usable after being archived?** The code still runs. The
data it returns stops reflecting current browser versions as time passes, and no
one will be fixing anything that breaks.

**What should I replace fake-useragent with?** Depends on what's actually consuming
the string. For a plain HTTP client with no real browser behind it, a small,
manually maintained list of current strings does the same job without depending on
a package's upkeep. For anything driving an actual browser, the better fix is
letting the browser report its own string rather than overriding it with any
database's output.

**Was fake-useragent's approach flawed even before the archive?** Not flawed for
what it was: a way to avoid one static, obviously-scripted string. Incomplete as a
standalone answer to "will this request look legitimate," because a string is one
field among many that get cross-checked against each other.

**Does this affect Playwright or Selenium users directly?** Only if the user agent
override was being sourced from this package rather than left as the browser's own.
[Setting a user agent at all changes what gets checked, not just what the field
says](playwright-user-agent.md).

**See also:** [why you should not set the user agent in Playwright](playwright-user-agent.md),
the underlying argument this package's archival makes concrete, and
[selenium-stealth hasn't been updated since November 2020](selenium-stealth-unmaintained.md),
another popular package in this space whose real status is easy to miss.

## Sources

- The package's own repository, [fake-useragent/fake-useragent](https://github.com/fake-useragent/fake-useragent),
  retrieved 2026-08-28, checked directly for its archive state and last
  activity date, rather than assumed from its continued presence in tutorials and
  existing automation code.
- [PyPI release history for fake-useragent](https://pypi.org/project/fake-useragent/),
  whose most recent version was uploaded in April 2025, independently confirming
  the string database stopped receiving updates before the repository itself was
  archived, retrieved 2026-08-29.
- GitHub's own documentation on [archiving a repository](https://docs.github.com/en/repositories/archiving-a-github-repository/archiving-repositories),
  confirming that an archived repository's issues and pull requests, not only its
  code, become read-only, retrieved 2026-08-29.

---

*From the notes of [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level, where the user agent string is the engine's own
and never comes from a database at all.*
