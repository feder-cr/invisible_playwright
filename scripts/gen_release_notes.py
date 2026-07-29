#!/usr/bin/env python3
"""Generate the GitHub release body for a firefox-N build from the actual
firefox_antidetect_patch commits that went into it.

The release tag (firefox-N) lives on the wrapper, but the binary's changes live
on the SOURCE repo (feder-cr/firefox_antidetect_patch). We never deep-clone that history
(it's a full Firefox fork); instead we use GitHub's compare API to list the
commits between the PREVIOUS release's source commit and this one, and turn their
subject lines into a short human-readable "What changed" list.

  - The previous release's source commit comes from its ``source-commit.txt``
    asset (this script's own output uploads one for the next run to read).
  - If there's no previous source commit (first automated release) or the compare
    fails, we fall back to a body WITHOUT the changelog section - publishing must
    never break on note generation.

This is NOT an LLM and NOT a raw ``git log`` dump: it filters out the
non-user-facing commits (docs/chore/ci/test/style) and prints the remaining
subjects as plain bullets. Quality rides on writing good commit subjects.

Usage:
    python scripts/gen_release_notes.py --tag firefox-10 --current <sha> \
        [--prev-sha <sha>] [--source-repo feder-cr/firefox_antidetect_patch] \
        [--firefox-version 151.0]
    # reads GITHUB_TOKEN from the env for the compare API (optional for public).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error

# Conventional-commit prefixes that never belong in user-facing release notes.
_SKIP = re.compile(r"^(docs|chore|ci|test|style|build)(\(|:)", re.I)


def _api(url: str, token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "invisible-playwright-release-notes"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def changelog_bullets(source_repo: str, prev_sha: str, current_sha: str,
                      token: str | None) -> list[str]:
    """Return the user-facing commit subjects in prev_sha..current_sha, or []."""
    if not prev_sha or not current_sha or prev_sha == current_sha:
        return []
    url = f"https://api.github.com/repos/{source_repo}/compare/{prev_sha}...{current_sha}"
    try:
        data = _api(url, token)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
        print(f"[gen_release_notes] compare API failed ({e}); no changelog section",
              file=sys.stderr)
        return []
    bullets: list[str] = []
    for c in data.get("commits", []):
        subject = (c.get("commit", {}).get("message") or "").splitlines()[0].strip()
        if not subject or _SKIP.match(subject):
            continue
        bullets.append(subject.rstrip("."))
    return bullets


def firefox_version() -> str:
    """The upstream Firefox version this build rebases on, or "" if unknown.

    Never hardcoded here. The number used to be written into the body as a
    literal, which meant the release notes kept announcing 150.0.1 after the
    binary had moved on, and a version marker that lies is a tell in its own
    right. The seal is the single source of truth; if invisible_core is not
    importable in the job that generates the notes, the sentence drops the
    number rather than guessing it.
    """
    try:
        from invisible_core import FIREFOX_UPSTREAM_VERSION  # noqa: PLC0415
    except Exception:
        return ""
    return str(FIREFOX_UPSTREAM_VERSION or "")


def build_body(tag: str, current_sha: str, bullets: list[str],
               ff_version: str = "") -> str:
    m = re.search(r"(\d+)", tag)
    n = int(m.group(1)) if m else None
    prev_label = f"firefox-{n - 1}" if n else "the previous build"
    short = (current_sha or "")[:8]

    headline = (f"Patched Firefox {ff_version}, the stealth build invisible_playwright drives."
                if ff_version
                else "The patched Firefox build invisible_playwright drives.")
    parts = [headline, ""]
    if bullets:
        parts.append(f"What changed since {prev_label}:")
        parts += [f"- {b}" for b in bullets]
        parts.append("")
    parts += [
        "Builds: Linux x86_64, Linux arm64, Windows x86_64, macOS arm64, macOS x86_64.",
        "",
        "Most people won't grab these by hand. The wrapper fetches the right one for "
        "your platform on first run:",
        "",
        "    pip install invisible-playwright",
        "",
        "If you do download manually, `checksums.txt` has the SHA256s. The macOS builds "
        "are ad-hoc signed (not notarized), so clear the quarantine flag: "
        "`xattr -dr com.apple.quarantine Firefox.app`",
    ]
    if short:
        parts += ["", f"Built from firefox_antidetect_patch @{short}."]
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="release tag, e.g. firefox-10")
    ap.add_argument("--current", required=True, help="firefox_antidetect_patch SHA this build was built from")
    ap.add_argument("--prev-sha", default="", help="previous release's source SHA (omit for none)")
    ap.add_argument("--source-repo", default="feder-cr/firefox_antidetect_patch")
    ap.add_argument("--firefox-version", default="",
                    help="upstream Firefox version of this build; read from the "
                         "seal via invisible_core when omitted")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    bullets = changelog_bullets(args.source_repo, args.prev_sha, args.current, token)
    ff = args.firefox_version or firefox_version()
    sys.stdout.write(build_body(args.tag, args.current, bullets, ff))
    return 0


if __name__ == "__main__":
    sys.exit(main())
