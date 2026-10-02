"""`set_input_files` with content instead of a path, and with a folder.

The client sends one of three shapes: `localPaths`, `localDirectory` for a
folder, and `payloads` (name, MIME type, base64 bytes) when the caller passes
`{"name", "mimeType", "buffer"}` instead of a path. The server read only the
first, so a payload became an EMPTY upload: the input was cleared, `change`
fired with no files, and the call returned as if it had worked. Measured on
0.25.7 with firefox-34: a payload `note.txt` reached the page as
`files.length == 0`.

A payload is now written to a file the session owns and goes through the same
engine command as a path. A folder is refused by name: handed to the engine it
took the tab down.
"""
from __future__ import annotations

import base64
import os

import pytest

from invisible_playwright._juggler.dispatcher import ProtocolException
from invisible_playwright._juggler.server import JugglerServer, _upload_paths


def _payload(name, data):
    return {"name": name, "mimeType": "text/plain",
            "buffer": base64.b64encode(data).decode()}


@pytest.mark.unit
def test_paths_pass_through_unchanged():
    server = JugglerServer()
    assert _upload_paths(server, {"localPaths": ["C:/a.txt", "C:/b.txt"]}) == [
        "C:/a.txt", "C:/b.txt"]
    assert getattr(server, "_upload_root", None) is None, (
        "a request with paths staged something")


@pytest.mark.unit
def test_a_payload_becomes_a_file_with_its_name_and_its_bytes():
    server = JugglerServer()
    try:
        paths = _upload_paths(server, {"payloads": [
            _payload("note.txt", b"hello payload"),
            _payload("note.txt", b"second, same name"),
        ]})
        assert [os.path.basename(p) for p in paths] == ["note.txt", "note.txt"]
        assert len(set(paths)) == 2, "two payloads with one name overwrote each other"
        with open(paths[0], "rb") as fh:
            assert fh.read() == b"hello payload"
        with open(paths[1], "rb") as fh:
            assert fh.read() == b"second, same name"
    finally:
        server.shutdown()
    assert not os.path.exists(paths[0]), "the staged files outlived the session"


@pytest.mark.unit
def test_a_payload_name_cannot_choose_where_it_is_written():
    server = JugglerServer()
    try:
        path, = _upload_paths(server, {"payloads": [
            _payload("../../escape.txt", b"x")]})
        assert os.path.basename(path) == "escape.txt"
        assert os.path.commonpath([path, server._upload_root]) == server._upload_root
        with pytest.raises(ProtocolException, match="needs a name"):
            _upload_paths(server, {"payloads": [_payload("", b"x")]})
    finally:
        server.shutdown()


@pytest.mark.unit
def test_an_empty_upload_is_still_a_way_to_clear_the_input():
    """`set_input_files([])` sends `payloads: []`: that one IS empty."""
    assert _upload_paths(JugglerServer(), {"payloads": []}) == []


@pytest.mark.unit
def test_a_folder_is_refused_by_name():
    with pytest.raises(ProtocolException, match="folder"):
        _upload_paths(JugglerServer(), {"localDirectory": "C:/somewhere"})


PAGE = """<!doctype html>
<input id="f" type="file" multiple>
<script>
window.got = [];
f.addEventListener('change', async e => {
  const files = [...e.target.files];
  got.push({names: files.map(x => x.name), sizes: files.map(x => x.size),
            texts: await Promise.all(files.map(x => x.text())),
            trusted: e.isTrusted});
});
</script>"""


@pytest.mark.e2e
def test_a_payload_reaches_the_page_as_a_file(firefox_binary):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=42, binary_path=firefox_binary,
                             humanize=False, headless=True) as browser:
        page = browser.new_page()
        page.set_content(PAGE)
        page.set_input_files("#f", [
            {"name": "note.txt", "mimeType": "text/plain", "buffer": b"hello payload"},
            {"name": "data.csv", "mimeType": "text/csv", "buffer": b"a,b\n1,2\n"},
        ], timeout=5000)
        page.wait_for_function("got.length > 0")
        assert page.evaluate("got") == [{
            "names": ["note.txt", "data.csv"], "sizes": [13, 8],
            "texts": ["hello payload", "a,b\n1,2\n"], "trusted": True}]
