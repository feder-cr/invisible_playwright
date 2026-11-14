#!/usr/bin/env bash
# HUNT SCAFFOLDING, NEVER MERGED: the three arms of the profile hunt, round by round, so the twin comparison does not drift with the runner.
# Usage: scripts/hunt_profile_rounds.sh OURS_BINARY STOCK_BINARY       (Windows: four processes at once)
# Arms (HUNT_ARM names the arm, HUNT_PROFILE=1 starts the Gecko profiler in the browser, HUNT_STAMPS=1 = the patched Juggler and our-side instruments):
#   ours_prof    ours, stamped, profiler from startup, a profile file per run
#   ours_noprof  ours, stamped, NO profiler (the twin: shows whether the profiler moves the timings)
#   stock_prof   stock Playwright Firefox, stamped, profiler, a profile file per browser (DEBUG=pw:browser copies the browser's output into the log)
# A round = each arm once, 4 invocations at once of 5 runs = 20 runs per arm; 3 rounds = 60 per arm. The order of the arms rotates each round.
# Every invocation under `timeout 300`. Logs: evidence/loaded_<arm>_<c>.log (read by scripts/hunt_profile_summary.py); profiles: prof/.
set +e
ours=$1
stock=$2
mkdir -p evidence prof
export HUNT_RUNS=5
export HUNT_STAMPS=1
export HUNT_PROFILE_DIR="$PWD/prof"
one_arm() {
  local arm=$1 engine=$2 bin=$3 prof=$4
  for c in 1 2 3 4; do
    (
      export HUNT_ARM="$arm"
      if [ "$prof" = 1 ]; then export HUNT_PROFILE=1; else unset HUNT_PROFILE; fi
      if [ "$engine" = stock ]; then export DEBUG=pw:browser; else unset DEBUG; fi
      timeout 300 python -u scripts/hunt_typing.py "$engine" "$bin" >> "evidence/loaded_${arm}_$c.log" 2>&1
      echo "arm $arm invocation $c exit $?"
    ) &
  done
  wait
}
declare -a ORDER
ORDER[1]="ours_prof ours_noprof stock_prof"
ORDER[2]="ours_noprof stock_prof ours_prof"
ORDER[3]="stock_prof ours_prof ours_noprof"
for round in 1 2 3; do
  for arm in ${ORDER[$round]}; do
    case "$arm" in
      ours_prof)   one_arm ours_prof ours "$ours" 1 ;;
      ours_noprof) one_arm ours_noprof ours "$ours" 0 ;;
      stock_prof)  one_arm stock_prof stock "$stock" 1 ;;
    esac
    echo "round $round arm $arm done; profiles on disk: $(ls prof 2>/dev/null | wc -l), firefox processes left: $(tasklist 2>/dev/null | grep -ic firefox)"
  done
done
for arm in ours_prof ours_noprof stock_prof; do
  echo "arm $arm: HUNT-TYPING lines $(cat evidence/loaded_${arm}_[0-9]*.log 2>/dev/null | grep -ac '^HUNT-TYPING '), HUNTKEY lines in logs $(cat evidence/loaded_${arm}_[0-9]*.log 2>/dev/null | grep -ac 'HUNTKEY ')"
done
exit 0
