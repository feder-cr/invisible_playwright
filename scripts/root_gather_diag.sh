#!/usr/bin/env bash
# One step of the root/ICE diagnosis (hunt, not a gate): run the realness gather of one exit and the ICE-name tests the way one leg of the gate does, and write
# down what the machine and the browser say. Needs FF_EXE (the engine), INVISIBLE_SEAL_FILE, and the wrapper and the core installed.
#
#   scripts/root_gather_diag.sh A   root, the line of the gate: sudo -H -E env -u PYTEST_ADDOPTS -u XDG_RUNTIME_DIR INVPW_REQUIRE_TRAP=1 PATH=$PATH python -m pytest ...
#   scripts/root_gather_diag.sh B   the runner's own user, no trap
#   scripts/root_gather_diag.sh C   as A, with the sandbox of the socket process and of the content processes switched off (a measurement, not a setting)
#   scripts/root_gather_diag.sh D   as A, without the resolver trap
#
# Output: diag/<step>/{before.txt,pytest.log,ps-*.txt,dmesg.txt,summary.txt,moz/}
set -u
STEP="${1:?step A, B, C or D}"
OUT="diag/$STEP"
rm -rf "$OUT"; mkdir -p "$OUT/moz"
ROOT="$PWD"

{
  echo "== step $STEP, $(date -u +%FT%TZ)"
  echo "== uname"; uname -a
  echo "== sysctl"; sysctl kernel.apparmor_restrict_unprivileged_userns kernel.unprivileged_userns_clone user.max_user_namespaces kernel.dmesg_restrict 2>&1
  echo "== id"; id
  echo "== os"; grep -E '^(NAME|VERSION)=' /etc/os-release
  echo "== resolv.conf"; ls -la /etc/resolv.conf; cat /etc/resolv.conf
  echo "== firefox"; ls -la "$FF_EXE"
} > "$OUT/before.txt" 2>&1

# the same tests as the gate leg; the trap off for D is a copy of the tests whose _trap_capability says no (the gate cannot do that: this is a measurement)
TESTS_DIR="tests"
if [ "$STEP" = "D" ]; then
  rm -rf ../w_notrap && cp -r "$ROOT" ../w_notrap
  python - <<'PY'
import pathlib
p = pathlib.Path("../w_notrap/tests/_leaks.py")
s = p.read_text()
s = s.replace("def _trap_capability() -> Tuple[bool, str]:\n", "def _trap_capability() -> Tuple[bool, str]:\n    return False, \"forced off for this measurement\"\n", 1)
p.write_text(s)
PY
  cd ../w_notrap || exit 1
fi

export MOZ_CRASHREPORTER_DISABLE=1
export MOZ_LOG="timestamp,sync,SOCKS-UDP:5,mtransport:5,SandboxBroker:5,SandboxTarget:5"
export MOZ_LOG_FILE="$ROOT/$OUT/moz/moz-%PID.log"
export INVPW_BINARY_PATH="$FF_EXE" STEALTHFOX_E2E_BINARY="$FF_EXE" INVISIBLE_CORE_AUTOFIX=off INVISIBLE_CORE_PIN=allow
FLAGS=(-m e2e -o addopts='' -o junit_family=xunit2 -q -p no:cacheprovider -p no:rerunfailures -rs)
REALNESS=(python -m pytest tests/test_realness_behind_a_declared_exit_e2e.py -k germany "${FLAGS[@]}")
ICE=(python -m pytest tests/test_ice_names_by_the_context_proxy_e2e.py "${FLAGS[@]}")

run_one() {   # the command
  case "$STEP" in
    A|D) sudo -H -E env -u PYTEST_ADDOPTS -u XDG_RUNTIME_DIR $([ "$STEP" = A ] && echo INVPW_REQUIRE_TRAP=1) "PATH=$PATH" "$@" ;;
    B)   env -u PYTEST_ADDOPTS "$@" ;;
    C)   sudo -H -E env -u PYTEST_ADDOPTS -u XDG_RUNTIME_DIR INVPW_REQUIRE_TRAP=1 MOZ_DISABLE_SOCKET_PROCESS_SANDBOX=1 MOZ_DISABLE_CONTENT_SANDBOX=1 "PATH=$PATH" "$@" ;;
  esac
}

run_named() {   # name, then the command: run it, and look at the processes while it runs
  local name="$1"; shift
  ( run_one "$@" ) > "$ROOT/$OUT/pytest-$name.log" 2>&1 &
  local pid=$!
  # a look at the processes in the middle of the run: is there a socket process, whose is it
  for t in 1 2 3 4 5 6; do
    sleep 8
    kill -0 "$pid" 2>/dev/null || break
    { echo "== t=$((t*8))s"; ps -eo pid,ppid,user,etime,args | grep -i -E "[f]irefox|contentproc" | cut -c1-260; } >> "$ROOT/$OUT/ps-$name.txt"
  done
  wait "$pid"
  echo "exit $?" >> "$ROOT/$OUT/pytest-$name.log"
}
run_named realness "${REALNESS[@]}"
run_named ice "${ICE[@]}"

cd "$ROOT" || exit 1
sudo dmesg 2>&1 | grep -i -E "apparmor|DENIED|seccomp|userns|namespace" | tail -60 > "$OUT/dmesg.txt" 2>&1

{
  echo "step $STEP"
  for name in realness ice; do
    echo "-- $name: $(grep -E '[0-9]+ (passed|failed)' "$OUT/pytest-$name.log" | tail -1)"
    echo "   'no candidate at all': $(grep -c 'produced no candidate at all' "$OUT/pytest-$name.log")"
  done
  echo "-- socket process in the ps snapshots: $(cat "$OUT"/ps-*.txt 2>/dev/null | grep -c -E 'contentproc.* socket( |$)') lines (users: $(cat "$OUT"/ps-*.txt 2>/dev/null | grep -E 'contentproc.* socket' | awk '{print $3}' | sort -u | tr '\n' ' '))"
  echo "-- moz logs of non-main processes that are not empty: $(find "$OUT/moz" -name '*child*' -size +0 | wc -l) of $(find "$OUT/moz" -name '*child*' | wc -l)"
  echo "-- 'UDP layer pushed' lines: parent $(cat "$OUT"/moz/*main* 2>/dev/null | grep -c 'layer pushed'), child $(cat "$OUT"/moz/*child* 2>/dev/null | grep -c 'layer pushed')"
  echo "-- 'Couldn't get default ICE candidate' (a gather that ran): $(cat "$OUT"/moz/*child* 2>/dev/null | grep -c "get default ICE candidate")"
  echo "-- refusals in the logs: $(cat "$OUT"/moz/* 2>/dev/null | grep -i -c -E 'denied|EPERM|EACCES|not permitted|not allowed|sandbox.*fail')"
  echo "-- dmesg lines: $(wc -l < "$OUT/dmesg.txt")"
} > "$OUT/summary.txt"
cat "$OUT/summary.txt"
exit 0
