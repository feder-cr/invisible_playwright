---
title: "How to scrape podcast transcripts with Playwright"
description: "Scrape podcast transcripts with Playwright: fetch the underlying VTT or SRT file instead of scrolling the panel, keep cue timing and text as rows, and detect speaker labels and auto-generation quality."
parent: "Scraping with Playwright"
grand_parent: "Guides"
nav_order: 141
---


# How to scrape podcast transcripts with Playwright

To scrape podcast transcripts with Playwright, find the direct link or API response
carrying the VTT or SRT file before touching the rendered panel, parse it into cues that
each keep a start time, an end time and text, normalize every timestamp to seconds while
keeping the original string, and record whether the transcript is auto-generated or
human-edited, because that one fact decides whether the text is safe to quote by the
sentence or only searchable as a block.

This is not the episode-listing problem. [Finding a show's episodes](how-to-scrape-podcast-episodes-playwright.md)
is about the feed, the guid and the archive. A transcript lives one level down, inside a
single episode, and it is a different shape of data entirely: not a list of items but a
list of timed cues, and the timing is the part worth keeping. Flatten a transcript into
one string too early and you can still search it, but you can no longer answer "where in
the episode does she say this", which is the question a transcript is usually scraped to
answer.

## A transcript is a list of cues, not a paragraph

The rendered transcript panel looks like a wall of text, sometimes broken into speaker
turns, sometimes not. Underneath, it is built from a sequence of cues, and each cue is a
small record: a start time, often an end time, and the words spoken in that span. A flat
string can be searched. A list of cues can be searched AND jumped to, because every match
still carries the moment it happened, which is the entire value of scraping a transcript
instead of copying it by hand.

Losing the cue boundaries is a one-way trip. Once forty cues have been joined into one
paragraph with a space between them, there is no reliable way to work backward to "this
sentence started at 14:32". Store the cue list itself, one row per cue, and generate any
flat text view from that afterward, never the other way around.

```python
# The unit worth keeping. Every field below survives to the stored row.
{
    "start_seconds": 872.4,
    "end_seconds": 876.1,
    "start_raw": "00:14:32.400",
    "speaker": "Host",          # None if the transcript does not label speakers
    "speaker_source": "field",  # "field", "inline", or "absent"
    "text": "So that's when we decided to rebuild the whole pipeline.",
}
```

## Fetch the VTT or SRT file instead of scrolling the panel

Plenty of podcast players render the transcript panel lazily, drawing only the cues near
the currently playing timestamp and swapping them out as playback moves forward. Reading
that panel means driving playback, or faking a scroll and a wait for every few seconds of
audio, for the length of the episode. An hour-long show becomes an hour-long scrape, and
a paused or muted player often renders nothing at all because the panel is wired to the
`timeupdate` event, not to a click.

The file behind that panel is almost always available as one request. Podcast platforms
that support transcripts typically expose the cue file as a `.vtt` or `.srt` document,
either linked directly in the page or returned by an API call the player itself makes to
populate the panel. Fetching that file is one request against a static document instead of
minutes of simulated scrolling, and [capturing the underlying API response](how-to-capture-xhr-api-responses-playwright.md)
is the general technique for finding it when there is no visible link.

```python
import re
from invisible_playwright import InvisiblePlaywright

TRANSCRIPT_LINK = "link[type='text/vtt'], a[href$='.vtt'], a[href$='.srt']"
TRANSCRIPT_IN_RESPONSE = re.compile(r"\.(?:vtt|srt)(?:\?|$)")


def fetch_vtt_or_srt(page, url):
    caught = {}

    def watch(response):
        if TRANSCRIPT_IN_RESPONSE.search(response.url):
            caught["body"] = response.text()

    page.on("response", watch)
    page.goto(url, wait_until="networkidle")

    node = page.query_selector(TRANSCRIPT_LINK)
    if node and "body" not in caught:
        caught["body"] = page.request.get(node.get_attribute("href")).text()

    if "body" not in caught:
        raise SystemExit("no VTT/SRT found; the panel is the only source")
    return caught["body"]


with InvisiblePlaywright(seed=42) as browser:
    page = browser.new_page()
    cue_text = fetch_vtt_or_srt(page, "https://example.com/show/episode-42")
```

Attach `page.on("response", ...)` before the navigation, or a request fired during page
load is missed. Try the link only as a fallback: the response listener already caught the
file if the player fetched it itself, and the link check costs nothing when it did not.

## Parse cues from VTT or SRT into one shape

WebVTT and SRT differ in punctuation but agree on structure: a block of numbered or
timestamped entries, each with a time range on its own line and the spoken text below it.
Parse both into the same row shape so the rest of the pipeline does not care which format
a given show used.

```python
import re

TIME_LINE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)


def timestamp_to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_cues(raw_text):
    """Works on VTT (dot milliseconds) and SRT (comma milliseconds) alike."""
    cues = []
    lines = raw_text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        match = TIME_LINE.search(lines[i])
        if not match:
            i += 1
            continue
        start_raw = lines[i].split("-->")[0].strip()
        h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
        text_lines = []
        i += 1
        while i < len(lines) and lines[i].strip() and not TIME_LINE.search(lines[i]):
            text_lines.append(lines[i].strip())
            i += 1
        cues.append({
            "start_seconds": timestamp_to_seconds(h1, m1, s1, ms1),
            "end_seconds": timestamp_to_seconds(h2, m2, s2, ms2),
            "start_raw": start_raw,
            "text": " ".join(text_lines).strip(),
        })
    return cues
```

Two things matter here more than the regex itself. The parser tolerates both delimiter
styles in the same function, because a batch of episodes from different tools will mix
them. And `start_raw` is kept next to `start_seconds`: seconds are what you sort, filter
and jump to, but the source string is what you show back to a person or hand to a media
player's own seek parameter, and reconstructing it from a float loses precision that the
original never had.

## Detect and merge speaker labels before you flatten anything

Speaker attribution shows up in two different shapes, and a transcript uses one or the
other, rarely both. Some feeds mark the speaker as a distinct field or a `<v Speaker>`
tag inside VTT. Others bake it into the text itself, as `Host: welcome back` at the start
of a line, with no separate field at all.

Detecting which form a file uses is a cheap check: try the structured tag first, and only
fall back to the inline pattern if the structured one is absent for the whole file.
Guessing per cue instead of per file causes worse damage than either format alone,
because a normal sentence that happens to start with a capitalized word and a colon,
"Note: this changed in season two", gets misread as a speaker turn.

```python
import re

VTT_VOICE_TAG = re.compile(r"^<v\s+([^>]+)>(.*)$")
INLINE_SPEAKER = re.compile(r"^([A-Z][\w .]{1,24}):\s*(.+)$")


def detect_speaker_form(cues):
    """One decision for the whole file, not one guess per cue."""
    if any(VTT_VOICE_TAG.match(c["text"]) for c in cues):
        return "tag"
    hits = sum(1 for c in cues if INLINE_SPEAKER.match(c["text"]))
    # require a clear majority, or a handful of coincidental matches
    # gets treated as if every cue in the file were labeled
    if hits >= max(3, len(cues) * 0.4):
        return "inline"
    return "absent"


def apply_speaker_form(cues, form):
    for cue in cues:
        if form == "tag":
            match = VTT_VOICE_TAG.match(cue["text"])
            cue["speaker"] = match.group(1) if match else cue.get("speaker")
            cue["text"] = match.group(2).strip() if match else cue["text"]
            cue["speaker_source"] = "tag" if match else "absent"
        elif form == "inline":
            match = INLINE_SPEAKER.match(cue["text"])
            cue["speaker"] = match.group(1) if match else None
            cue["text"] = match.group(2).strip() if match else cue["text"]
            cue["speaker_source"] = "inline" if match else "absent"
        else:
            cue["speaker"] = None
            cue["speaker_source"] = "absent"
    return cues
```

Store `speaker_source` alongside `speaker`. A cue whose label came from a distinct tag is
a claim the transcription tool made deliberately; a cue whose label came out of a colon
match is an inference, and knowing which one you have matters the first time two speakers'
turns run together without a blank line between them.

## Record whether the transcript is auto-generated

The single fact worth capturing before anything else is whether a transcript came from
speech-to-text or from a person editing it. Auto-generated transcripts frequently drop
speaker labels entirely, punctuate on pauses rather than grammar, and mis-hear proper
nouns and technical terms. Human-edited transcripts usually carry reliable punctuation
and correctly labeled turns, because someone read the audio against the text.

Most platforms that distinguish the two say so somewhere on the page, as a badge, a
caption near the transcript tab, or a field in the API response that served the VTT file.
Look for it before you parse: a human-edited transcript can be quoted sentence by
sentence with confidence in the wording, while an auto-generated one is safer treated as a
fuzzy full-text index into the audio, where any one cue's exact phrasing is a probability
rather than a fact.

```python
def transcript_quality(page):
    """Look for an explicit signal before assuming either way."""
    badge = page.query_selector("[data-transcript-type], .transcript-badge")
    if badge:
        label = (badge.get_attribute("data-transcript-type")
                 or badge.text_content() or "").strip().lower()
        if "auto" in label or "generated" in label:
            return "auto_generated"
        if "edited" in label or "human" in label:
            return "human_edited"
    return "unknown"
```

Store the result as its own field next to the cue list, not folded into a comment.
`"unknown"` is a real, honest value here. Guessing auto-generation from the text quality
alone, no punctuation, no speaker labels, is possible but noisy enough that it belongs in
a separate, clearly-named heuristic field rather than mixed with a page's own declared
signal.

## Assemble the stored row, keep source strings, drop nothing

Bring the pieces together into one row per episode: the cue list with timing, speakers
and text, plus the two facts that describe the whole transcript rather than one cue.
Nothing here needs its own network round trip beyond the single file fetch above.

```python
def scrape_transcript(page, url):
    cue_text = fetch_vtt_or_srt(page, url)   # the fetch shown earlier
    cues = parse_cues(cue_text)
    form = detect_speaker_form(cues)
    cues = apply_speaker_form(cues, form)
    return {
        "cues": cues,
        "speaker_form": form,
        "quality": transcript_quality(page),
        "cue_count": len(cues),
    }
```

`cue_count` is a cheap sanity check worth keeping on the row: a transcript that parsed to
zero or one cue almost always means the fetch returned an error page or an empty file
rather than a real absence of cues, and it is easier to catch that from a stored number
than from re-reading every row by hand later.

## Where this stops: rendered images and playback-gated text

Some players draw the transcript as an image, generated server-side as part of a
"shareable quote" card, with no text node and no underlying file behind it. Others reveal
transcript text only while audio is actively playing, tied to a playback event with no
static document and no API response carrying the full cue list ahead of time. Neither
case has a file to fetch, and neither has a DOM node worth reading.

That is a real limit, not a missing selector. Re-transcribing the audio yourself, running
it through a speech-to-text model, is a legitimate answer to that gap, but it is a
different technique with its own accuracy and cost tradeoffs, and it is out of scope for
a page about reading a transcript the page already produced.

## Conclusion

A podcast transcript is worth more as a list of timed cues than as a paragraph, and the
fastest way to get that list is the VTT or SRT file behind the panel, not the panel
itself. Fetch that file first, parse both timestamp styles into seconds while keeping the
source string, detect whether speaker labels sit in their own field or inside the text,
and record whether the transcript came from speech-to-text or a human editor, because
that single fact tells the next person whether a cue is a quote or an approximation.
When there is no file and no text node, a rendered image or a playback-gated panel, stop
there: that gap is a different job.

## Short answers to the questions that lead here

**Should I scroll the transcript panel to read it?** Only when there is nothing else.
Many panels lazy-render near the current playback position, which makes reading the whole
transcript as slow as playing the episode. Check for a direct `.vtt`/`.srt` link or the
API response that feeds the panel first.

**How do I normalize SRT and VTT timestamps together?** Convert both to seconds with one
parser: VTT uses a dot before milliseconds, SRT uses a comma, and both otherwise follow
`HH:MM:SS`. Keep the original string in a separate field, because a media player's seek
parameter and a citation both want the source format, not a recomputed one.

**How do I know if speaker labels are a field or part of the text?** Check the whole file
for a structured tag first, such as VTT's `<v Speaker>`. Only fall back to matching
"Name:" at the start of a line if no structured tag appears anywhere, and require several
matches before trusting the inline pattern, since one coincidental colon is not a
transcript convention.

**Why does it matter if a transcript is auto-generated?** Auto-generated transcripts
often lack speaker labels and can mis-hear words, so a single cue's exact wording is
closer to a probability than a fact. Human-edited transcripts are safer to quote
sentence by sentence. Record which kind you got when the page says so.

**What if there is no file and the panel needs audio playing to show text?** That is a
real stop point. A transcript rendered only as an image, or revealed only during active
playback with no static file or API response, has to be re-transcribed from the audio,
which is a different task than scraping text the page already has.

## Sources

- The [WebVTT specification](https://www.w3.org/TR/webvtt1/), including the `<v Speaker>`
  voice tag and the `HH:MM:SS.mmm` timestamp format used for cue timing.
- Playwright's [`Page.on("response")`](https://playwright.dev/python/docs/api/class-page#page-event-response)
  and [`APIRequestContext.get()`](https://playwright.dev/python/docs/api/class-apirequestcontext#api-request-context-get),
  used exactly as documented upstream, since the browser this library returns is a real
  Playwright `Browser`. Retrieved 2026-08-28.
- SRT's `HH:MM:SS,mmm --> HH:MM:SS,mmm` cue format, the comma-delimited counterpart to
  WebVTT's dot, as implemented by every common SRT-producing tool this timestamp regex was
  checked against.

**See also:** [scraping podcast episode listings](how-to-scrape-podcast-episodes-playwright.md)
for finding the feed and keying episodes before you ever reach a transcript,
[capturing XHR and API responses](how-to-capture-xhr-api-responses-playwright.md) for the
general technique behind finding the VTT/SRT request, [cleaning scraped prices and
dates](how-to-clean-scraped-prices-and-dates-playwright.md) for the same
normalize-and-keep-the-source habit applied elsewhere, and
[scraping video listings and metadata](how-to-scrape-video-listings-and-metadata-playwright.md)
for the sibling problem on video pages that carry captions instead of an audio transcript.

---

*Written while maintaining [invisible_playwright](https://github.com/oss-invisible/playwright),
a Firefox patched at the C++ level driven by stock Playwright. The first version of this
scraper joined every cue into one string before storing it, and the day someone asked
"what minute does she mention the merger" there was no way to answer without re-parsing
the original file from scratch.*
