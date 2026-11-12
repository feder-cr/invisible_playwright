#!/usr/bin/env bash
# The second look at the ICE gather that finds nothing under root (a hunt, not a gate). The first one (root-gather-diag, run 43203007725) showed that the candidates come back
# only with the sandbox of the socket and content processes switched off. This one asks WHAT the sandbox refuses and whether the published engine does the same.
#
#   scripts/root_gather_diag2.sh pytest   step A of the first run (the gate's line, root, realness [germany]) with the browser's stderr kept and the processes listed unclipped
#   scripts/root_gather_diag2.sh probe    a gather outside pytest, as root, by engine (the engine of the gate run 43188313516 / the published firefox-62), sandbox on / off,
#                                         no proxy / behind a local gateway that relays UDP
# Needs FF_EXE + INVISIBLE_SEAL_FILE (the engine of the gate run), PUB_PY + PUB_EXE (a venv with invisible-core 62.65.0 + invisible-playwright 0.50.1 and the published engine).
# Output: diag2/...
set -u
MODE="${1:?pytest or probe}"
ROOT="$PWD"
OUT="$ROOT/diag2"
mkdir -p "$OUT"

snapshot_ps() {   # file
  { echo "== $(date -u +%T)"; ps -eo pid,ppid,user,stat,etime,args -ww | grep -i -E "[f]irefox|contentproc" ; } >> "$1"
}

after_the_run() {  # dir
  local d="$1"
  { echo "== coredumpctl"; (coredumpctl list --no-pager 2>&1 | tail -20) ; echo "== journal kernel, seccomp / audit / firefox";
    (sudo journalctl -k --no-pager 2>&1 | grep -i -E "seccomp|firefox|type=1326|SIGSYS" | tail -40) ; echo "== dmesg"; (sudo dmesg 2>&1 | grep -i -E "seccomp|firefox|type=1326|SIGSYS|DENIED" | tail -40); echo "== /var/crash"; ls -la /var/crash 2>&1 | head; } > "$d/after.txt" 2>&1
  { echo "== process types that wrote on the pipe (Parent/Socket/Child/GPU...): $(cat "$d"/browser-*.log 2>/dev/null | grep -o -E "\[(Parent|Socket|Child|GPU|RDD|Utility)[ 0-9]*" | sed -E "s/ [0-9]+//" | sort | uniq -c | tr "
" " ")";
    echo "== socket-process mtransport/SOCKS-UDP lines: $(cat "$d"/browser-*.log 2>/dev/null | grep -c -E "\[Socket [0-9]+: .*(mtransport|SOCKS-UDP)")"; echo "== 'layer pushed' (socket process): $(cat "$d"/browser-*.log 2>/dev/null | grep -c -E "\[Socket [0-9]+: .*layer pushed")";
    echo "== lines with Sandbox / seccomp / violation / SIGSYS / crashed in the browser output"; grep -h -i -E "sandbox|seccomp|violation|SIGSYS|crash|terminated|died" "$d"/browser-*.log 2>/dev/null | sort | uniq -c | sort -rn | head -80; } > "$d/sandbox-lines.txt"
}

run_pytest_A() {   # NAME EXE: step A of the first run with the binary EXE; the files go to diag2/NAME
  local NAME="$1" EXE="$2"
  D="$OUT/$NAME"; rm -rf "$D"; mkdir -p "$D/moz"
  export DIAG_OUT="$D"
  export MOZ_CRASHREPORTER_DISABLE=1 MOZ_SANDBOX_LOGGING=1
  # MOZ_LOG goes to STDERR (no MOZ_LOG_FILE): the sandboxed socket process cannot create a file, and its stderr is the pipe the wrapper reads
  export MOZ_LOG="timestamp,sync,SOCKS-UDP:5,mtransport:5,SandboxBroker:5,SandboxTarget:5"
  unset MOZ_LOG_FILE
  export INVPW_BINARY_PATH="$EXE" STEALTHFOX_E2E_BINARY="$EXE" INVISIBLE_CORE_AUTOFIX=off INVISIBLE_CORE_PIN=allow
  ( sudo -H -E env -u PYTEST_ADDOPTS -u XDG_RUNTIME_DIR INVPW_REQUIRE_TRAP=1 "PATH=$PATH" "PYTHONPATH=$ROOT/scripts/diag_plugin" "DIAG_OUT=$D" \
      python -m pytest tests/test_realness_behind_a_declared_exit_e2e.py -k germany -p diag_browser_output -m e2e -o addopts='' -o junit_family=xunit2 -q -p no:cacheprovider -p no:rerunfailures -rs ) > "$D/pytest.log" 2>&1 &
  pid=$!
  for t in 1 2 3 4 5 6 7 8; do sleep 6; kill -0 "$pid" 2>/dev/null || break; snapshot_ps "$D/ps.txt"; done
  wait "$pid"; echo "exit $?" >> "$D/pytest.log"
  snapshot_ps "$D/ps.txt"
  after_the_run "$D"
  { echo "pytest A with the stderr kept:"; grep -E "[0-9]+ (passed|failed)" "$D/pytest.log" | tail -1; echo "  'no candidate at all': $(grep -c 'produced no candidate at all' "$D/pytest.log")";
    echo "  browser output files: $(ls "$D"/browser-*.log 2>/dev/null | wc -l); 'seccomp sandbox violation' lines: $(cat "$D"/browser-*.log 2>/dev/null | grep -c 'seccomp sandbox violation')";
    grep -h 'seccomp sandbox violation' "$D"/browser-*.log 2>/dev/null | sed -E 's/pid [0-9]+, tid [0-9]+/pid N, tid M/' | sort | uniq -c | sort -rn | head -10;
    echo "  socket processes in the last ps (unclipped, type is the last word):"; grep -h -E "contentproc.* socket( |$)" "$D/ps.txt" | tail -3 | cut -c1-200; } > "$D/summary.txt"
  cat "$D/summary.txt"
}

case "$MODE" in
pytest)
  run_pytest_A pytest-A "$FF_EXE"
  # the same step with the engine in a directory of root (755), not under the runner's home: is it the environment of the gate or the engine?
  sudo rm -rf /opt/ff && sudo mkdir -p /opt/ff && sudo cp -a "$(dirname "$FF_EXE")/." /opt/ff/ && sudo chown -R root:root /opt/ff && sudo chmod 755 /opt /opt/ff
  ls -ld "$(dirname "$FF_EXE")" "$(dirname "$(dirname "$FF_EXE")")" /opt/ff > "$OUT/rootcopy-perms.txt" 2>&1
  run_pytest_A pytest-A-rootcopy /opt/ff/firefox
  ;;
probe)
  export MOZ_CRASHREPORTER_DISABLE=1 MOZ_SANDBOX_LOGGING=1
  export MOZ_LOG="timestamp,sync,SOCKS-UDP:5,mtransport:5,SandboxBroker:5,SandboxTarget:5"
  unset MOZ_LOG_FILE
  : > "$OUT/probes.jsonl"
  for engine in gate62 pub; do
    for sandbox in on off; do
      for proxy in noproxy proxy tcponly; do
        name="$engine-sandbox_$sandbox-$proxy"; D="$OUT/probe-$name"; mkdir -p "$D"
        if [ "$engine" = gate62 ]; then PY=python; EXE="$FF_EXE"; SEAL="INVISIBLE_SEAL_FILE=$INVISIBLE_SEAL_FILE"; else PY="$PUB_PY"; EXE="$PUB_EXE"; SEAL="-u INVISIBLE_SEAL_FILE"; fi
        OFF=""; [ "$sandbox" = off ] && OFF="MOZ_DISABLE_SOCKET_PROCESS_SANDBOX=1 MOZ_DISABLE_CONTENT_SANDBOX=1"
        ARG=""; [ "$proxy" != noproxy ] && ARG="$proxy"
        ( sudo -H -E env -u PYTEST_ADDOPTS -u XDG_RUNTIME_DIR $SEAL $OFF INVISIBLE_CORE_PIN=allow INVISIBLE_CORE_AUTOFIX=off "DIAG_OUT=$D" "PATH=$PATH" "$PY" scripts/ice_gather_probe.py "$name" "$EXE" $ARG ) > "$D/probe.out" 2>&1 &
        pid=$!
        for t in 1 2 3; do sleep 7; kill -0 "$pid" 2>/dev/null || break; snapshot_ps "$D/ps.txt"; done
        wait "$pid"; snapshot_ps "$D/ps.txt"
        grep -h '^{' "$D/probe.out" >> "$OUT/probes.jsonl" || echo "{\"label\": \"$name\", \"exception\": \"no json line\"}" >> "$OUT/probes.jsonl"
        after_the_run "$D"
        echo "$name: $(grep -h '^{' "$D/probe.out" | cut -c1-220)"
      done
    done
  done
  python - <<'PY'
import json
print("%-34s %-9s %-26s %s" % ("probe", "gathered", "types", "errors / exception"))
for line in open("diag2/probes.jsonl"):
    r = json.loads(line)
    print("%-34s %-9s %-26s %s" % (r.get("label"), r.get("gathered"), ",".join(r.get("candidate_types", [])), r.get("exception") or r.get("errors")))
PY
  ;;
esac
exit 0
