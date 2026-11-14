#!/usr/bin/env bash
# HUNT SCAFFOLDING, NEVER MERGED: one arm of the engine hunt.
# Usage: scripts/hunt_engine_arm.sh ARM ours|stock BINARY      (env: HUNT_MODE=alone|loaded, HUNT_STAMPS=1 for a patched engine, DEBUG=pw:browser for stock's)
# 5 runs per invocation of hunt_typing.py, every invocation under `timeout 300`.
# alone: 12 invocations one after the other = 60 runs. loaded: 3 rounds of four at once = 4 processes, 12 invocations, 60 runs.
# The logs are evidence/alone_<arm>.log or evidence/loaded_<arm>_<n>.log, which scripts/hunt_engine_summary.py reads.
set +e
arm=$1
engine=$2
bin=$3
mkdir -p evidence
export HUNT_ARM="$arm"
export HUNT_RUNS=5
if [ "$HUNT_MODE" = "alone" ]; then
  for n in 1 2 3 4 5 6 7 8 9 10 11 12; do
    timeout 300 python -u scripts/hunt_typing.py "$engine" "$bin" >> "evidence/alone_${arm}.log" 2>&1
    echo "arm $arm alone $n exit $?"
  done
else
  for round in 1 2 3; do
    for c in 1 2 3 4; do
      timeout 300 python -u scripts/hunt_typing.py "$engine" "$bin" >> "evidence/loaded_${arm}_$c.log" 2>&1 &
    done
    wait
    echo "arm $arm loaded round $round done"
  done
fi
echo "arm $arm: HUNT-TYPING lines $(cat evidence/*_${arm}.log evidence/*_${arm}_[0-9]*.log 2>/dev/null | grep -ac '^HUNT-TYPING '), HUNTKEY lines in logs $(cat evidence/*_${arm}.log evidence/*_${arm}_[0-9]*.log 2>/dev/null | grep -ac 'HUNTKEY ')"
exit 0
