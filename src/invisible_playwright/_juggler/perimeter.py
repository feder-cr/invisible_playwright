"""What this package deliberately does NOT do, and why - one name at a time.

⛔ THIS FILE EXISTS SO A REFUSAL CAN NAME THE FEATURE. Without it an
out-of-perimeter call reaches a guid that was never created and comes back as
`no object 'artifact@3' to answer 'read'` - technically true, useless to read,
and indistinguishable from a bug in this package. With it the caller is told
that reading an artifact is tracing, that tracing is outside the automation
core, and that the refusal is a decision rather than a gap.

⛔ AND IT LIVES HERE, IN THE SHIPPED PACKAGE, not in the workbench. The
perimeter was written first in a workbench script that measures how much work
is left, and that is the wrong home for it twice over: the thing that has to
REFUSE is this package, and a list kept somewhere that is never shipped is a
second copy of a fact. The workbench inventory now reads this module (rule 16).

The perimeter itself was chosen by the owner on 2026-08-27: the automation
core, with tracing, HAR, video, codegen and the recorder, `APIRequestContext`,
`CDPSession` and the clock left out. See `32-stacco-da-playwright.md` section 1.
"""
from __future__ import annotations

#: operation name -> the FEATURE it belongs to. The feature, not a sentence:
#: the sentence a caller reads is built below, and one word per name keeps this
#: table something a person can check against `_impl` by eye.
OUTSIDE = {
    # APIRequestContext
    "disposeAPIResponse":              "APIRequestContext",
    "fetch":                           "APIRequestContext",
    "fetchLog":                        "APIRequestContext",
    "fetchResponseBody":               "APIRequestContext",
    "newRequest":                      "APIRequestContext",
    # artifacts: traces, videos and downloads
    "cancel":                          "artifacts: traces, videos and downloads",
    "delete":                          "artifacts: traces, videos and downloads",
    # CDP
    "connectOverCDP":                  "CDP",
    "detach":                          "CDP",
    "newBrowserCDPSession":            "CDP",
    "newCDPSession":                   "CDP",
    "send":                            "CDP",
    # HAR
    "harClose":                        "HAR",
    "harExport":                       "HAR",
    "harLookup":                       "HAR",
    "harOpen":                         "HAR",
    "harStart":                        "HAR",
    "harUnzip":                        "HAR",
    # an artifact stream
    "read":                            "an artifact stream",
    # WebAuthn
    "credentialsCreate":               "WebAuthn",
    "credentialsDelete":               "WebAuthn",
    "credentialsGet":                  "WebAuthn",
    "credentialsInstall":              "WebAuthn",
    # capture
    "pdf":                             "capture",
    # clock
    "clockFastForward":                "clock",
    "clockInstall":                    "clock",
    "clockPauseAt":                    "clock",
    "clockResume":                     "clock",
    "clockRunFor":                     "clock",
    "clockSetFixedTime":               "clock",
    "clockSetSystemTime":              "clock",
    # the streamed upload path - set_input_files with local paths works
    "createTempFiles":                 "the streamed upload path - set_input_files with local paths works",
    # recorder
    "cancelPickLocator":               "recorder",
    "createSelectorForTest":           "recorder",
    "hideHighlight":                   "recorder",
    "highlight":                       "recorder",
    "next":                            "recorder",
    "pause":                           "recorder",
    "pickLocator":                     "recorder",
    "registerLocatorHandler":          "recorder",
    "requestPause":                    "recorder",
    "resolveLocatorHandlerNoReply":    "recorder",
    "resume":                          "recorder",
    "runTo":                           "recorder",
    "unregisterLocatorHandler":        "recorder",
    # the driver's WebSocket server, which is what is being removed
    "closePage":                       "the driver's WebSocket server, which is what is being removed",
    "closeServer":                     "the driver's WebSocket server, which is what is being removed",
    "connect":                         "the driver's WebSocket server, which is what is being removed",
    "ensureOpened":                    "the driver's WebSocket server, which is what is being removed",
    "sendToPage":                      "the driver's WebSocket server, which is what is being removed",
    "sendToServer":                    "the driver's WebSocket server, which is what is being removed",
    "startServer":                     "the driver's WebSocket server, which is what is being removed",
    "stopServer":                      "the driver's WebSocket server, which is what is being removed",
    # tracing
    "__waitInfo__":                    "tracing",
    "addStackToTracingNoReply":        "tracing",
    "pathAfterFinished":               "tracing",
    "saveAsStream":                    "tracing",
    "startTracing":                    "tracing",
    "stopTracing":                     "tracing",
    "stream":                          "tracing",
    "traceDiscarded":                  "tracing",
    "tracingGroup":                    "tracing",
    "tracingGroupEnd":                 "tracing",
    "tracingStart":                    "tracing",
    "tracingStartChunk":               "tracing",
    "tracingStarted":                  "tracing",
    "tracingStop":                     "tracing",
    "tracingStopChunk":                "tracing",
    "write":                           "tracing",
    "zip":                             "tracing",
    # video annotations. ⛔ `screencastStart` and `screencastStop` are NOT
    # here any more: since the engine's screencast came back (firefox-28) the
    # server answers them with live JPEG frames of the window. What stays out
    # is the overlay the driver used to paint on the recording - chapters,
    # action captions - which needs the recorder this package does not ship.
    "screencastChapter":               "video",
    "screencastHideActions":           "video",
    "screencastRemoveOverlay":         "video",
    "screencastSetOverlayVisible":     "video",
    "screencastShowActions":           "video",
    "screencastShowOverlay":           "video",
}

#: ⛔ ANSWERED THOUGH OUTSIDE. These are the ones the CLIENT sends on its own
#: while closing a session or annotating a wait. Refusing them would turn every
#: clean `close()` into an error about a feature the caller never asked for, and
#: would fill the handler log on every `expect()`. They answer the minimum and
#: do nothing.
#:
#: The list is short on purpose: an entry here is a piece of perimeter coming
#: back in through the window, so each one names who calls it.
COURTESY = {
    "tracingStop": "the client calls it inside context.close()",
    "tracingStopChunk": "the client calls it inside context.close()",
    "addStackToTracingNoReply": "the client sends it while tracing is off",
    "traceDiscarded": "the client sends it at the end of a session",
    "__waitInfo__": "the client annotates every wait for the trace viewer",
}


def refusal(operation):
    """The sentence a caller gets, or an empty string if it is in perimeter."""
    feature = OUTSIDE.get(operation)
    if not feature:
        return ""
    return (operation + " is part of " + feature + ", which "
            "invisible_playwright does not implement: the package covers the "
            "automation core, and " + feature + " is outside it by decision, "
            "not by omission. See 32-stacco-da-playwright.md section 5.4.")
