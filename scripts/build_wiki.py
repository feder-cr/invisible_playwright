#!/usr/bin/env python3
"""Render the Jekyll docs/ tree into a GitHub wiki checkout.

Usage: build_wiki.py <docs_dir> <out_dir>

A GitHub wiki is a flat set of <Page>.md files with no Jekyll front matter and a
_Sidebar.md for navigation. This converter, for every docs/*.md:
  - strips the YAML front matter (the body already starts with the H1),
  - rewrites internal links [x](slug.md[#a]) -> [x](slug[#a]) (wiki has no .md),
  - names the page by its slug (stable URLs matching the docs site), except
    index.md -> Home.md (the wiki landing page),
and then generates _Sidebar.md mirroring the parent/has_children/nav_order tree.

With WIKI_VIEW_PIXEL set it also ends every page with an image whose URL carries
that page's name - see the VIEW PIXEL block below for what those counts are and,
more importantly, what they are not.
"""
import os, re, sys
from urllib.parse import quote

DOCS = sys.argv[1]
OUT = sys.argv[2]

# ─── VIEW PIXEL ──────────────────────────────────────────────────────────────
# GitHub publishes no per-page numbers for a wiki. The Traffic API counts the
# REPOSITORY - `/traffic/views` is clones-and-visits, `/traffic/popular/paths`
# is the top ten paths over fourteen days - so with 300+ pages here it can tell
# you that the wiki is read and never which page. There is no wiki equivalent,
# and the wiki engine that would have one (Gollum, which GitHub open-sourced and
# whose fork serves these pages) is not the code running on github.com.
#
# What a wiki page CAN do is reference an image. So each page ends with one
# whose URL carries the page name, and whatever serves that URL does the
# counting. The endpoint is not this script's business: it is a URL template.
#
# OFF unless WIKI_VIEW_PIXEL is set, and there is deliberately no default. A URL
# baked in here would point every reader of every fork at a third party nobody
# in that fork chose, and it would do it silently, from a docs build. The
# workflow passes the value in from a repository VARIABLE, so switching this on
# or off is a settings change rather than a commit.
#
# WHAT THE NUMBERS ARE WORTH. A wiki page never reaches the reader's browser
# with your URL in it: GitHub rewrites every external image to
# camo.githubusercontent.com and serves it from its own cache. A "hit" is
# therefore "camo fetched this page's pixel", which collapses repeat readers
# behind a warm cache entry and counts crawlers as readers. It ranks pages
# against each other; it is not a visitor count, and nothing downstream should
# present it as one.
#
# `_Sidebar.md` gets no pixel on purpose. It renders on every page, so its
# single URL would be one bucket for the whole wiki AND the most cached object
# on it - the least informative counter available.
PIXEL = os.environ.get("WIKI_VIEW_PIXEL", "").strip()
if PIXEL and "{page}" not in PIXEL:
    raise SystemExit(
        "WIKI_VIEW_PIXEL has no {page} placeholder: %r. Every page would fetch "
        "the same URL, so the counter would have one bucket for the whole wiki "
        "- which is the failure this is meant to fix, arrived at quietly. Put "
        "{page} where the page name belongs, e.g. "
        "https://example.com/wiki/{page}.svg" % PIXEL)
if PIXEL and not PIXEL.startswith("https://"):
    raise SystemExit(
        "WIKI_VIEW_PIXEL must be https:// - got %r. GitHub will not load a "
        "plain-http image into a wiki page, so the pixel would be a broken "
        "image on 300+ pages and count nothing." % PIXEL)

def pixel(name):
    """The image line closing page `name`, or "" when the feature is off.

    Empty alt text: this is a decorative pixel, and a screen reader announcing
    a filename at the end of every page is worse than silence. An endpoint that
    serves a VISIBLE badge instead should be given a caption here.
    """
    if not PIXEL:
        return ""
    return "\n![](%s)\n" % PIXEL.replace("{page}", quote(name, safe=""))


def parse(path):
    t = open(path, encoding="utf-8").read()
    fm, body = {}, t
    m = re.match(r'^---\n(.*?)\n---\n?(.*)$', t, re.S)
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r'([A-Za-z_]+):\s*(.*?)\s*$', line)
            if mm:
                v = mm.group(2)
                if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                    v = v[1:-1]
                fm[mm.group(1)] = v
        body = m.group(2)
    return fm, body.lstrip("\n")

# Subdirectories are walked, not skipped. `docs/` grew `integrations/` with nine
# pages, and a converter reading only the flat level dropped all nine AND left
# every link into them pointing at a wiki page that does not exist - nine dead
# links across eight pages, invisible because the pages themselves rendered.
# A wiki is flat, so a subdirectory page is named by its own slug, except an
# `index`/`README` inside a directory, which takes the DIRECTORY's name so that
# a `](integrations/)` link has somewhere to land.
pages = {}
sources = {}
for dirpath, _dirnames, filenames in os.walk(DOCS):
    rel = os.path.relpath(dirpath, DOCS).replace(os.sep, "/")
    for f in sorted(filenames):
        if not f.endswith(".md"):
            continue
        stem = f[:-3]
        if rel == ".":
            slug = stem
            src = stem
        elif stem in ("index", "README"):
            slug = rel.split("/")[-1]
            src = rel
        else:
            slug = stem
            src = rel + "/" + stem
        if slug in pages:
            raise SystemExit(
                "two docs pages want the same flat wiki name %r: %s and %s. "
                "A wiki has no directories, so this has to be resolved in "
                "docs/ rather than silently letting one overwrite the other."
                % (slug, sources[slug], src))
        pages[slug] = parse(os.path.join(dirpath, f))
        sources[slug] = src

valid = set(pages.keys())
#: Every way a doc page can be addressed from another one, mapped to its flat
#: wiki name: bare slug, `dir/slug`, either with `.md`, and `dir/` on its own.
addressable = {src: slug for slug, src in sources.items()}
for slug, src in sources.items():
    if "/" in src:
        addressable.setdefault(src.split("/")[-1], slug)
# `dir/` on its own is how a page links to a directory index, and it needs the
# trailing form for EVERY source, not only the ones that already carry a slash:
# `integrations/README.md` has the source `integrations`, so the first cut of
# this left `](integrations/)` as the single dead link out of 325 pages.
for src in list(addressable):
    addressable.setdefault(src + "/", addressable[src])

def rewrite(body):
    def repl(m):
        target, anchor = m.group(1), (m.group(2) or "")
        hit = addressable.get(target) or (target if target in valid else None)
        if hit:
            return "](" + ("Home" if hit == "index" else hit) + anchor + ")"
        return m.group(0)
    # `[a-z0-9/-]` and an optional `.md`: a link into a subdirectory carries the
    # slash and may or may not carry the extension, and the first version of
    # this pattern matched neither.
    return re.sub(r'\]\(([a-z0-9/\-]+)(?:\.md)?(#[A-Za-z0-9\-]+)?\)', repl, body)

os.makedirs(OUT, exist_ok=True)
written = 0
for slug, (fm, body) in pages.items():
    name = "Home" if slug == "index" else slug
    # The pixel is appended AFTER rewrite(): its URL is absolute and rewrite()
    # only matches bare relative targets, but a link rewriter is exactly the
    # kind of thing that grows a case later, and this ordering means it can
    # never reach the one URL on the page that must survive verbatim.
    open(os.path.join(OUT, name + ".md"), "w", encoding="utf-8", newline="\n").write(
        rewrite(body) + "\n" + pixel(name))
    written += 1

def title_of(slug):
    return pages[slug][0].get("title", slug)

def link(slug):
    return "[%s](%s)" % (title_of(slug), "Home" if slug == "index" else slug)

def children_of(group_title):
    kids = [(s, fm) for s, (fm, b) in pages.items() if fm.get("parent") == group_title]
    kids.sort(key=lambda x: int(x[1].get("nav_order", "999")))
    return kids

toplevel = [(s, fm) for s, (fm, b) in pages.items() if not fm.get("parent") and s != "index"]
toplevel.sort(key=lambda x: int(x[1].get("nav_order", "999")))

lines = ["### " + link("index"), ""]
for slug, fm in toplevel:
    t = title_of(slug)
    lines.append("**%s**" % t)
    for cs, cfm in children_of(t):
        if cfm.get("has_children") == "true":
            lines.append("- %s" % link(cs))
            for gs, gfm in children_of(cfm.get("title", cs)):
                lines.append("  - %s" % link(gs))
        else:
            lines.append("- %s" % link(cs))
    lines.append("")
open(os.path.join(OUT, "_Sidebar.md"), "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")

print("wrote %d pages + _Sidebar.md to %s%s"
      % (written, OUT, " (with view pixel)" if PIXEL else ""))
