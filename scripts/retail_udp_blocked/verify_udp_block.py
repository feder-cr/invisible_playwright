r"""Proof of the network condition, printed as JSON and checked: UDP is dropped (or free, for the control) and TCP/DNS still work.

Ported from verify_udp_block.py of the Linux reference (private study repo, captures/h3-fallback/retail-159-linux/<run>/
udp_block_check.json). Same probes and same keys: udp_dns_8.8.8.8_53, udp_stun_stun.l.google.com_19302, tcp_8.8.8.8_53,
getaddrinfo_ms. Changes for Windows and for the new job:
  - the Linux probes sent 12 zero bytes, which no server answers, so they proved nothing in the control. Here the datagram is
    a real STUN Binding Request (and a real DNS query for the DNS probe), so a server answers it when UDP flows;
  - --expect control|blocked turns the printout into a check: control needs a STUN answer, blocked needs none (silently
    dropped or refused by the send), and both need TCP and name resolution; the exit code is 1 when the condition is wrong;
  - the Linux rule dropped UDP port 53 too and resolved names over TCP (glibc use-vc); the Windows resolver is a service
    that speaks UDP/53, so the rule leaves UDP port 53 open (see udp_block.ps1). udp_dns_8.8.8.8_53 therefore answers in both
    conditions here, is recorded, and is not part of the check;
  - a second STUN server (the one the local page uses) and one TCP probe to an h3 site are added.
"""
import argparse, json, os, secrets, socket, struct, sys, time

STUN_SERVERS = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478)]
TCP_SITE = ("cloudflare-quic.com", 443)
TIMEOUT_S = 3


def stun_binding_request():
    """20-byte STUN Binding Request (RFC 5389): type 0x0001, length 0, magic cookie, random 96-bit transaction id."""
    txid = secrets.token_bytes(12)
    return struct.pack(">HHI", 0x0001, 0, 0x2112A442) + txid, txid


def dns_query(name="example.com"):
    """A plain DNS query (type A, class IN) with a random id."""
    qid = secrets.token_bytes(2)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return qid + struct.pack(">HHHHH", 0x0100, 1, 0, 0, 0) + qname + struct.pack(">HH", 1, 1), qid


def udp_probe(host, port, payload, accept):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT_S)
    try:
        s.sendto(payload, (host, port))
        data, _ = s.recvfrom(1024)
        return "reply" if accept(data) else "unexpected reply (%d bytes)" % len(data)
    except socket.timeout:
        return "no reply in %d s (silently dropped)" % TIMEOUT_S
    except OSError as e:
        return "error: %s" % e
    finally:
        s.close()


def stun_probe(host, port, attempts):
    """First answer wins; the result is the last outcome when none answered. Also returns every attempt."""
    seen = []
    for _ in range(attempts):
        req, txid = stun_binding_request()
        r = udp_probe(host, port, req, lambda d: len(d) >= 20 and d[:2] == b"\x01\x01" and d[8:20] == txid)
        seen.append(r)
        if r == "reply":
            break
    return seen[-1], seen


def tcp_probe(host, port):
    t = time.time()
    try:
        socket.create_connection((host, port), timeout=5).close()
        return "connected in %d ms" % int((time.time() - t) * 1000)
    except OSError as e:
        return "error: %s" % e


def probe_all(attempts):
    out, stun_all = {}, {}
    q, qid = dns_query()
    out["udp_dns_8.8.8.8_53"] = udp_probe("8.8.8.8", 53, q, lambda d: d[:2] == qid)
    for host, port in STUN_SERVERS:
        try:
            addr = socket.gethostbyname(host)
        except OSError as e:
            out["udp_stun_%s_%d" % (host, port)] = "error: resolve %s" % e
            continue
        out["udp_stun_%s_%d" % (host, port)], stun_all["%s_%d" % (host, port)] = stun_probe(addr, port, attempts)
    out["tcp_8.8.8.8_53"] = tcp_probe("8.8.8.8", 53)
    out["tcp_%s_%d" % TCP_SITE] = tcp_probe(*TCP_SITE)
    t = time.time()
    try:
        out["getaddrinfo_%s" % TCP_SITE[0]] = sorted({a[4][0] for a in socket.getaddrinfo(TCP_SITE[0], TCP_SITE[1])})
    except OSError as e:
        out["getaddrinfo_%s" % TCP_SITE[0]] = "error: %s" % e
    out["getaddrinfo_ms"] = int((time.time() - t) * 1000)
    out["stun_attempts"] = stun_all
    return out


def judge(out, expect):
    """List of what is wrong with the condition; empty means the condition holds."""
    bad = []
    stun = [v for k, v in out.items() if k.startswith("udp_stun_")]
    if expect == "control":
        if not any(v == "reply" for v in stun):
            bad.append("control: no STUN server answered the Binding Request, UDP is not flowing")
    else:
        answered = [k for k, v in out.items() if k.startswith("udp_stun_") and (v == "reply" or str(v).startswith("unexpected reply"))]
        if answered:
            bad.append("blocked: a STUN server answered (%s), UDP is not blocked" % ", ".join(answered))
        if not stun:
            bad.append("blocked: no STUN probe ran")
    for k, v in out.items():
        if k.startswith("tcp_") and not str(v).startswith("connected"):
            bad.append("%s: TCP does not connect (%s)" % (expect, k))
    ga = out.get("getaddrinfo_%s" % TCP_SITE[0])
    if not isinstance(ga, list) or not ga:
        bad.append("%s: name resolution failed (%s)" % (expect, ga))
    return bad


def block_mode(out):
    """How the block shows to a sender: 'silent drop' (no answer) or 'send error' (the send itself fails)."""
    stun = [v for k, v in out.items() if k.startswith("udp_stun_")]
    if any(str(v).startswith("error") for v in stun):
        return "send error"
    return "silent drop"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", choices=["control", "blocked"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--attempts", type=int, default=None, help="STUN attempts per server (default 3 for control, 2 for blocked)")
    a = ap.parse_args(argv)
    attempts = a.attempts or (3 if a.expect == "control" else 2)
    out = probe_all(attempts)
    bad = judge(out, a.expect)
    result = dict(out)
    result["expect"] = a.expect
    result["ok"] = not bad
    result["failures"] = bad
    if a.expect == "blocked":
        result["block_mode"] = block_mode(out)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(json.dumps(result, indent=1))
    if bad:
        print("::error::condition proof failed: " + "; ".join(bad))
        sys.exit(1)


if __name__ == "__main__":
    main()
