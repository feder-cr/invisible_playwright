#!/usr/bin/env bash
# HUNT SCAFFOLDING, NEVER MERGED: the three arms of the pipe-buffer hunt, interleaved so a slow minute of the runner is spread over all of them.
# Usage: scripts/hunt_engine_arm.sh OURS_BINARY STOCK_BINARY      (env: HUNT_STAMPS=1 for the patched engines)
# Arms: ours_bigpipe (the candidate, the core's two protocol pipes made with CreatePipe(None, 1 << 20)), ours_normal (the same, the core as it
# is), stock (stock Playwright's Firefox, DEBUG=pw:browser so its driver copies the browser's output into the log).
# Windows, 4 processes at once. 3 rounds; in each round the three arms run one after the other (the order rotates per round), each as 4 processes at
# once x 5 runs, every invocation of hunt_typing.py under `timeout 300`: 3 rounds x 4 x 5 = 60 runs per arm.
# The logs are evidence/loaded_<arm>_<n>.log, which scripts/hunt_engine_summary.py reads.
set +e
ours=$1
stock=$2
mkdir -p evidence
export HUNT_RUNS=5
for round in 1 2 3; do
  case $round in
    1) order="ours_bigpipe ours_normal stock";;
    2) order="ours_normal stock ours_bigpipe";;
    3) order="stock ours_bigpipe ours_normal";;
  esac
  for arm in $order; do
    case $arm in
      ours_bigpipe) engine=ours;  bin=$ours;  big=1;;
      ours_normal)  engine=ours;  bin=$ours;  big=0;;
      stock)        engine=stock; bin=$stock; big=0;;
    esac
    for c in 1 2 3 4; do
      (
        export HUNT_ARM="$arm" HUNT_BIGPIPE="$big"
        if [ "$arm" = "stock" ]; then export DEBUG=pw:browser; fi
        timeout 300 python -u scripts/hunt_typing.py "$engine" "$bin" >> "evidence/loaded_${arm}_$c.log" 2>&1
        echo "arm $arm round $round process $c exit $?"
      ) &
    done
    wait
    echo "arm $arm round $round done"
  done
done
for arm in ours_bigpipe ours_normal stock; do
  echo "arm $arm: HUNT-TYPING lines $(cat evidence/loaded_${arm}_[0-9]*.log 2>/dev/null | grep -ac '^HUNT-TYPING '), HUNTKEY lines in logs $(cat evidence/loaded_${arm}_[0-9]*.log 2>/dev/null | grep -ac 'HUNTKEY '), HUNT-PIPE lines $(cat evidence/loaded_${arm}_[0-9]*.log 2>/dev/null | grep -ac '^HUNT-PIPE ')"
done
exit 0
