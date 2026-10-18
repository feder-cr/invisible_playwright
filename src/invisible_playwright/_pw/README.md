# Playwright's Python client, vendored

A modified copy of `playwright` **1.61.0** (`_impl`, `sync_api`, `async_api`),
Apache-2.0 (`LICENSE` in this folder). The namespace is
`invisible_playwright._pw` instead of `playwright`, so it never collides with a
stock `playwright` installed alongside it. What answers this client is the
in-process server in `../_juggler/`; Playwright's Node driver is not shipped
(`../THIRD_PARTY_FORK.md` has the licence terms and the history).

## What differs from upstream

**The imports.** Every absolute `playwright...` import is rewritten to this
namespace, anchored at the start of the line, because the word `playwright` also
appears in docstrings, documentation URLs, examples and two local variables.
`tests/test_fork.py` keeps it that way: a new `from playwright...` line would
still run, against the installed package, and silently stop being this code.

**The behaviour, in these files only.** Each change carries a
`# MODIFIED by invisible_playwright:` comment at the point it is made, which is
also where the repository's English gate reads it.

| file | what changed |
|---|---|
| `_impl/_browser.py` | the numbers of the pages the script asks for, 0, 1, 2, in the order of the calls, reserved before the first `await`; the only counter of them |
| `_impl/_browser_context.py` | each page the script asks for carries its number in the session, reserved on the client at the moment of the call, where the order of the calls exists |
| `_impl/_connection.py` | the execution-context guarantee is extracted out of `dispatch`, so a fused type (`Dialog`) gets it too: without it a sync `dialog.accept()` inside the `dialog` callback hangs |
| `_impl/_playwright.py` | asking for `chromium` or `webkit` raises a `ValueError` that says they were removed, not disabled |
| `sync_api/_generated.py`, `async_api/_generated.py` | the `chromium` and `webkit` properties raise `NotImplementedError` with the same explanation, instead of an `AttributeError` |
| `sync_api/_context_manager.py`, `async_api/_context_manager.py` | the transport comes from `make_transport` (the in-process server), and the impl-side objects are bound for the fused types |

Everything else is upstream, byte for byte.

## Regenerating

`playwright==1.61.0` stays among the **development** dependencies on purpose: it is
the source this folder is regenerated from, and the reference it is compared
against. The changes above are re-applied by their markers.
