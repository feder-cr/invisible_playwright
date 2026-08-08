---
title: "How to download files with Playwright"
description: "Download files with Playwright using expect_download and save_as, keeping the transfer on the browser's proxied, DNS-through-proxy exit and off your real IP."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 10
---


# How to download files with Playwright

The Playwright download API is small and it is the whole story: wait for a
download with `page.expect_download()`, then write the bytes with
`download.save_as()`. There is nothing to learn beyond the upstream docs.

So this page spends most of its length on the part that is easy to get wrong
without noticing: where the bytes travel. A file you pull with a separate HTTP
client leaves from your host and announces your real address. A file the browser
downloads leaves from the same exit the rest of the session uses. That
difference is the reason to let the browser do it.

## The download API is unchanged, because the Browser is real

Switching from stock Playwright is two lines, and after that every method you
already know works the same:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/report")
```

The object `InvisiblePlaywright` hands back is a real
`playwright.sync_api.Browser`. There is no wrapped subset of the API and no
download-specific shim to look up. `page.expect_download()`,
`download.save_as()`, `download.path()`, `download.suggested_filename` and
`download.url` all behave exactly as documented upstream, because they are the
upstream methods running on the upstream object.

If you already have a Playwright download routine, paste it in and it runs.

## Waiting for a download with expect_download

A download starts when the page navigates to a resource the browser cannot
render, or when a script calls a download. You cannot know the moment in
advance, so you wrap the action that triggers it in `expect_download()` and read
the result off the returned object:

```python
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/exports")

    with page.expect_download() as download_info:
        page.click("#download-csv")   # whatever kicks off the transfer

    download = download_info.value
    print("server suggested:", download.suggested_filename)
    print("source url:", download.url)
```

The `with` block returns as soon as the download begins, not when it finishes.
The `download` object is your handle to a transfer that may still be running. It
carries the filename the server proposed (`suggested_filename`) and the URL the
bytes came from (`url`), both of which are useful for naming and logging without
trusting anything the page put in the DOM.

## Where the bytes actually go: save_as

Playwright streams the download to a temporary location first. Nothing is at a
path you control until you ask for one. `save_as()` moves it and blocks until
the transfer is complete:

```python
from pathlib import Path
from invisible_playwright import InvisiblePlaywright

with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    page.goto("https://example.com/exports")

    with page.expect_download() as download_info:
        page.click("#download-csv")

    download = download_info.value
    target = Path("downloads") / download.suggested_filename
    target.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(target)          # blocks until the file is fully written

    print("saved", target, target.stat().st_size, "bytes")
```

Two habits worth keeping. Build the destination name from
`suggested_filename` rather than from link text, and always assert a non-zero
size afterwards. An empty file that saved without raising is the download
equivalent of a page that came back blank: it did not error, and it is still a
failure.

## The part that matters: the download rides the browser's proxied path

Here is the reason to do the download inside the browser instead of grabbing the
URL with a separate HTTP client.

When you set a proxy, the whole session goes through it, and the file transfer
is part of the session:

```python
proxy = {
    "server": "socks5://gate.example.com:1080",
    "username": "user",
    "password": "pass",
}

with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/exports")

    with page.expect_download() as download_info:
        page.click("#download-csv")

    download_info.value.save_as("report.csv")
    # the bytes for report.csv left from the proxy exit, not from this host
```

The download egresses through the same proxy the browser uses, and DNS is
routed through the proxy by default, so the lookup for the download host does
not resolve on your machine either. The file fetch takes the same exit, the
same address and the same name resolution as every page load in the session.

Contrast the shortcut people reach for. It is tempting to read
`download.url`, hand it to a plain HTTP client, and fetch the file directly:

```python
# DO NOT do this behind a proxy:
import requests
requests.get(download.url)     # leaves from THIS host, on your real IP,
                               # and resolves the hostname on your local DNS
```

That request leaves from your host on your real address, and it resolves the
hostname on your local resolver. To the server, one session just fetched a page
from a residential exit in one country and then pulled the file it links to from
a datacenter IP in another, with a DNS lookup to match. That is a self-inflicted
mismatch of exactly the kind detectors are built to notice, and it is the same
family of leak as [WebRTC reporting your real address next to the proxy's](webrtc-leak-proxy.md).
Let the browser download the file and the mismatch never exists.

The SOCKS5 authentication and DNS-through-proxy specifics, including the parts
most guides get wrong, are in
[Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md).

## Downloads that begin from a click, not a link

Not every download has a URL you could fetch even if you wanted to. A button
that posts a form, assembles a blob in JavaScript and triggers a download has no
stable link to hand to an HTTP client at all. The `expect_download()` pattern
does not care how the download started, so the same code covers it:

```python
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()
    page.goto("https://example.com/reports")

    page.select_option("#format", "xlsx")
    with page.expect_download() as download_info:
        page.click("#generate")       # posts a form, builds the file server-side

    download = download_info.value
    download.save_as(download.suggested_filename)
```

Because the click arcs to the button on a curved path rather than teleporting,
the interaction that produces the file looks like an interaction. For sessions
where a site watches behaviour rather than fingerprints, that matters as much as
the exit address does, and it is covered in
[the checklist for being detected on one site](playwright-detected-as-bot.md).

## Verifying the download used the proxy, not your host

Do not assume the path; confirm it. Before the download, read the exit address
the browser actually has, from inside the browser, and check the download host
resolves through the session rather than your machine:

```python
with InvisiblePlaywright(seed=42, proxy=proxy) as browser:
    page = browser.new_page()

    page.goto("https://example.com/ip")     # any endpoint that echoes the caller IP
    exit_ip = page.inner_text("body")
    print("session exits from:", exit_ip)   # this is the address the download uses too

    page.goto("https://example.com/exports")
    with page.expect_download() as download_info:
        page.click("#download-csv")
    download_info.value.save_as("report.csv")
```

If `exit_ip` is the proxy's, the download left from there. This is the same
principle as testing through the proxy you deploy with rather than on localhost,
described in
[how to test whether your browser is detected](how-to-test-bot-detection.md):
measure the path you actually use, do not assume it. When you cycle exits between
jobs, the download follows whichever proxy that session was launched with, which
is the model in
[how to rotate proxies with Playwright](how-to-rotate-proxies-playwright.md).

## Conclusion

The download itself is three lines: `expect_download()`, read the value,
`save_as()`. The value of doing it this way is not the API, it is the routing.
Because the browser the wrapper returns is a real Playwright Browser, the
transfer inherits the session's proxy and its DNS-through-proxy resolution, so
the file fetch leaves from the same exit as every page load instead of from your
host. Reach for a separate HTTP client and you trade three lines for a leak.
Keep the download in the browser and there is nothing to leak.

## Short answers to the questions that lead here

**How do I download a file with Playwright?** Wrap the trigger in
`with page.expect_download() as download_info:`, then call
`download_info.value.save_as("path")`. `save_as` blocks until the file is fully
written.

**Do I need a special API with invisible_playwright?** No. The object you get
back is a real Playwright Browser, so `expect_download` and `save_as` are the
standard upstream methods with no wrapper on top.

**Does the download go through my proxy?** Yes. The transfer uses the same
proxy exit as the rest of the session, and DNS is routed through the proxy by
default, so neither the fetch nor its hostname lookup touches your host.

**Can I just requests.get the download URL instead?** You can, and behind a
proxy you should not: that request leaves from your real IP and resolves on your
local DNS, creating a mismatch with the exit the browser is using.

**Where does the file go before save_as?** To a temporary location Playwright
manages. It is not at a path you control until `save_as()` moves it, or until you
read `download.path()`.

**How do I name the file the way the server intended?** Use
`download.suggested_filename`, which is the name the server proposed, rather than
scraping link text from the page.

## Sources

- [The Playwright download API](https://playwright.dev/python/docs/downloads)
  (`page.expect_download`, `Download.save_as`, `suggested_filename`, `url`,
  `path`), which runs unchanged on the real Browser the wrapper returns.
- This project's proxy behaviour: SOCKS5, HTTP and HTTPS schemes with DNS routed
  through the proxy by default, documented in [Configuration](configuration.md).

**See also:** [Playwright SOCKS5 proxy with authentication](playwright-socks5-proxy-authentication.md)
for the DNS-through-proxy details, [how to upload files with Playwright](how-to-upload-files-playwright.md)
for the reverse operation, and [Configuration](configuration.md) for the proxy dict
and timezone handling every session shares.

---

*Written while maintaining [invisible_playwright](https://github.com/feder-cr/invisible_playwright),
a Firefox patched at the C++ level driven by stock Playwright. The download is the
easy part; keeping it on the browser's own exit is the point.*
