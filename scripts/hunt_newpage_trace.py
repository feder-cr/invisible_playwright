"""HUNT SCAFFOLDING, NEVER MERGED: trace every call the in-process transport takes and answers, and the steps of op_new_page.

Writes JSON lines to $INVPW_HUNT_POPUP_TRACE (the same file as the popup trace): {"method": "CALL"/"REPLY"/"REPLY_ERROR", "id",
"call", "guid"} from transport._work, and {"method": "NEWPAGE <step>", ...} from BrowserContextDispatcher.op_new_page.
Run from the checkout: python scripts/hunt_newpage_trace.py
"""
import io

T = "src/invisible_playwright/_juggler/transport.py"
s = io.open(T, encoding="utf-8").read()
old = """            message = item
            try:
                result = self._server.handle(message)
            except Exception as failure:
                self.reply_error(message.get("id"), failure)
            else:
                self.reply(message.get("id"), result)
"""
new = """            message = item
            _hunt_t(method="CALL", id=message.get("id"), call=message.get("method"), guid=message.get("guid"))
            try:
                result = self._server.handle(message)
            except Exception as failure:
                _hunt_t(method="REPLY_ERROR", id=message.get("id"), call=message.get("method"), error=str(failure)[:200])
                self.reply_error(message.get("id"), failure)
            else:
                _hunt_t(method="REPLY", id=message.get("id"), call=message.get("method"))
                self.reply(message.get("id"), result)
"""
assert s.count(old) == 1, "transport _work"
s = s.replace(old, new, 1)
s = s.rstrip("\n") + '''


def _hunt_t(**row):
    import json as _j, os as _o, threading as _th, time as _t
    path = _o.environ.get("INVPW_HUNT_POPUP_TRACE")
    if not path:
        return
    row["t"] = round(_t.time(), 4)
    row["pid"] = _o.getpid()
    row["thread"] = _th.current_thread().name
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(_j.dumps(row) + "\\n")
    except Exception:
        pass
'''
io.open(T, "w", encoding="utf-8", newline="\n").write(s)

S = "src/invisible_playwright/_juggler/server.py"
s = io.open(S, encoding="utf-8").read()
old = """        with browser._asking(self.context_id) as answered:
            result = self.conn.send("Browser.newPage",
                                    _address(self.context_id, {}), timeout=None)
            answered(result["targetId"], number)
        page = browser._wait_for_page(result["targetId"], timeout=60.0)
        return {"page": page.channel}
"""
new = """        _hunt_trace(method="NEWPAGE asked", target=None, url=self.context_id)
        with browser._asking(self.context_id) as answered:
            result = self.conn.send("Browser.newPage",
                                    _address(self.context_id, {}), timeout=None)
            _hunt_trace(method="NEWPAGE engine answered", target=result.get("targetId"))
            answered(result["targetId"], number)
        _hunt_trace(method="NEWPAGE waiting for the page", target=result.get("targetId"))
        try:
            page = browser._wait_for_page(result["targetId"], timeout=60.0)
        except Exception as exc:
            _hunt_trace(method="NEWPAGE wait failed", target=result.get("targetId"), error=str(exc)[:200])
            raise
        _hunt_trace(method="NEWPAGE got the page", target=result.get("targetId"))
        return {"page": page.channel}
"""
assert s.count(old) == 1, "op_new_page"
s = s.replace(old, new, 1)
io.open(S, "w", encoding="utf-8", newline="\n").write(s)
print("patched newpage trace")
