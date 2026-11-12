#!/usr/bin/env bash
# The background-traffic hunt: for each proxy kind and each run, the four arms at the same time (each with its own proxy and its own X server), then the table.
#   scripts/bg_traffic_run.sh RUNS IDLE_SECONDS     needs OURS_EXE, RETAIL_EXE, and the stock Playwright Firefox installed
set -u
RUNS="${1:-5}"; IDLE="${2:-120}"
OUT="$PWD/bgt"; mkdir -p "$OUT"
for kind in http socks; do
  for run in $(seq 1 "$RUNS"); do
    echo "== $kind run $run $(date -u +%T)"
    for arm in ours retail retail-gd stock; do
      xvfb-run -a -s "-screen 0 1280x1024x24" python scripts/bg_traffic.py "$arm" "$kind" "$run" "$OUT" "$IDLE" > "$OUT/$arm-$kind-$run.log" 2>&1 &
    done
    wait
    tail -qn1 "$OUT"/*-"$kind"-"$run".log
  done
done
python scripts/bg_traffic_table.py "$OUT" | tee "$OUT/table.txt"
exit 0
