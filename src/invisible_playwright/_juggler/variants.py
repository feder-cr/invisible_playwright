"""The shapes a message takes across Playwright's protocol versions, brought to
the one this server reads.

⛔ ONE PLACE, AT THE ENTRANCE. The client is whatever version the caller has
installed, and the protocol moved between versions: in 1.62 the `timeout` left
the parameters of nearly every command and went to the call's metadata, and a
few commands and optional fields were added. Reading each variant where the
operation uses it would put a second spelling of every timeout in a hundred
places; folding them here means `server.py` reads one shape, `_caller_timeout`
reads `params["timeout"]` as it always did, and a new version is a change to
this file.

⛔ NO VERSION IS ASKED FOR, AND NONE IS REFUSED. The two forms do not conflict
- a message carries its timeout in the parameters or in the metadata, never
meaningfully in both - so the message is read by what it contains: a timeout in
the parameters wins, one in the metadata fills the gap, and a field this
server does not know is carried along unread, as the protocol's own optional
fields are.

What each 1.62 addition becomes here:

  * `metadata.timeout` (milliseconds, `0` = no limit, as in the parameters):
    read into `params["timeout"]` when the parameters carry none.
  * `scroll` ("auto" | "none") on click, dblclick, hover, tap, check, uncheck,
    dragAndDrop: IGNORED ON PURPOSE. Every pointer action here scrolls the
    target into view first, which is `auto`; `none` asks to act on the element
    where it is, and honouring it needs an action path that does not scroll,
    which does not exist. The action still happens, scrolled.
  * `noGlobal` on exposeBinding: moot, `exposeBinding` is refused as a whole.
  * `type` "webp" on screenshot: REFUSED. The engine captures PNG and JPEG, and
    answering a webp request with PNG bytes would hand the caller a file that
    is not the format it asked for.
  * `selector` / `strict` on waitForFunction (the predicate runs against an
    element): REFUSED when a selector is given. Running the predicate without
    the element would answer a different question.
  * `credentials` (virtual WebAuthn credentials) in storageState,
    setStorageState and a context's `storageState`: REFUSED when asked for or
    given; WebAuthn is outside the perimeter (see `perimeter.py`). `False`,
    `None` and an empty list are not a request and pass.
  * `Page.screencastFrameAck`: REFUSED. This server acknowledges a frame to
    the engine itself as soon as it has relayed it, so there is no frame held
    back for the client to release.
  * `Frame.drop`: already answered by `FrameDispatcher.op_drop`, which refuses
    it with its own reason (the engine has no command for it).
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _metadata_timeout(metadata: Dict) -> Optional[float]:
    wanted = metadata.get("timeout")
    if isinstance(wanted, bool) or not isinstance(wanted, (int, float)):
        return None
    return float(wanted) if wanted >= 0 else None


def _refuse(reason: str):
    # Imported here: `dispatcher.py` imports this module.
    from .dispatcher import ProtocolException
    raise ProtocolException(reason)


def _present(value: Any) -> bool:
    """A request for something, as opposed to false, null or an empty list."""
    return bool(value)


def to_internal(method: Optional[str], params: Dict, metadata: Dict) -> Dict:
    """`params` in the shape the operations read, or a refusal naming why this
    one command is not served. `params` itself is never changed."""
    out = dict(params)

    if out.get("timeout") is None:
        out.pop("timeout", None)
        wanted = _metadata_timeout(metadata or {})
        if wanted is not None:
            out["timeout"] = wanted

    if method == "screencastFrameAck":
        _refuse("screencastFrameAck is not served: this server acknowledges "
                "each screencast frame to the engine itself as soon as it has "
                "relayed it, so the client has no frame to release")
    if method in ("screenshot", "expectScreenshot") and out.get("type") == "webp":
        _refuse("a webp screenshot is not available: the engine captures PNG "
                "and JPEG only, and a PNG sent as webp would be a file that is "
                "not the format asked for")
    if method == "waitForFunction" and out.get("selector"):
        _refuse("wait_for_function() on an element (a selector) is not "
                "available: the predicate would run without the element it "
                "was written for")
    if method in ("storageState", "setStorageState", "newContext",
                  "launchPersistentContext"):
        state = out.get("storageState")
        if _present(out.get("credentials")) or (
                isinstance(state, dict) and _present(state.get("credentials"))):
            _refuse("virtual credentials in a storage state are not available: "
                    "WebAuthn is outside the perimeter this package covers")
    return out
