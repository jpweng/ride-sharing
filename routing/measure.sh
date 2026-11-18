#!/usr/bin/env bash
# Measures a routing image built by routing/build.sh: start-up time, memory
# and latency of the route queries the app makes, served three ways:
#   in RAM      osrm-routed's default, data loaded into its own memory
#   mmap        --mmap: data files mapped, read through the page cache
#   mmap, 256M  --mmap in a container limited to 256 MB, as on a small server
#
#   routing/measure.sh <image>   (appends a Markdown table to $REPORT)
#
# "anon" is the server's own memory, "file" the page cache of the mapped
# data (the kernel can drop it under memory pressure and read it again).
set -euo pipefail

IMAGE=${1:?usage: routing/measure.sh <image>}
HERE=$(cd "$(dirname "$0")" && pwd)
REPORT=${REPORT:-$HERE/.work/report.md}
PORT=5099
NAME=osrm-measure

# Town centres all over the country, (lon,lat): every ordered pair is a query.
PLACES=(
  6.1432,46.2044 6.6323,46.5197 6.6411,46.7785 6.9107,46.4312 7.1619,46.8065 7.3606,46.2331
  7.9876,46.3159 6.9293,46.9900 7.3445,47.3649 7.2467,47.1368 7.4474,46.9480 7.6280,46.7580
  7.8632,46.6863 7.9033,47.3500 7.5886,47.5596 8.3093,47.0502 8.5155,47.1662 8.5417,47.3769
  8.7237,47.4988 8.6349,47.6973 9.3767,47.4245 9.5320,46.8508 8.9511,46.0037 9.0244,46.1946
  8.7943,46.1670 9.8355,46.4908 10.2369,46.6283
)

mem_stat() { docker exec "$NAME" sh -c 'grep -E "^(anon|file) " /sys/fs/cgroup/memory.stat' | awk '{printf "%s %d MB, ", $1, $2 / 1048576}'; }

queries() {
  # Prints "<HTTP status> <latency in ms>" for each query, one per line.
  local a b
  for a in "${PLACES[@]}"; do
    for b in "${PLACES[@]}"; do
      [ "$a" = "$b" ] && continue
      curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' \
        "http://localhost:$PORT/route/v1/driving/$a;$b?overview=full&geometries=geojson" \
        | awk '{printf "%s %d\n", $1, $2 * 1000}' || echo "000 0"
    done
  done
}

summary() {
  sort -k2 -n | awk '
    $1 != 200 {failed++; next}
    {v[++n] = $2; s += $2}
    END {
      if (n == 0) {printf "all %d failed", failed; exit}
      i50 = int(n * 0.5) + 1; i95 = int(n * 0.95) + 1
      if (i95 > n) i95 = n
      printf "%d ok, %d failed; mean %d ms, p50 %d ms, p95 %d ms, max %d ms", n, failed, s / n, v[i50], v[i95], v[n]
    }'
}

run() {
  local label=$1; shift
  docker rm -f "$NAME" > /dev/null 2>&1 || true
  local start ready
  start=$(date +%s%N)
  docker run -d --name "$NAME" -p "$PORT:5000" "$@" > /dev/null
  local up=""
  for _ in $(seq 1 900); do
    if curl -fs -o /dev/null "http://localhost:$PORT/route/v1/driving/7.4474,46.9480;7.4500,46.9500"; then up=1; break; fi
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || break
    sleep 0.2
  done
  ready=$(( ($(date +%s%N) - start) / 1000000 ))
  if [ -z "$up" ]; then
    echo "| $label | never ready: $(docker inspect -f 'exit {{.State.ExitCode}}, OOM-killed {{.State.OOMKilled}}' "$NAME") | | | |" >> "$REPORT"
    docker logs "$NAME" 2>&1 | tail -20 >&2
    docker rm -f "$NAME" > /dev/null
    return
  fi
  local cold warm
  cold=$(queries | summary)
  warm=$(queries | summary)
  echo "| $label | $ready ms | $(mem_stat || echo "container gone: ")$(docker inspect -f 'OOM-killed: {{.State.OOMKilled}}' "$NAME") | $cold | $warm |" >> "$REPORT"
  docker logs "$NAME" 2>&1 | tail -5 >&2
  docker rm -f "$NAME" > /dev/null
}

{
  echo
  echo "| Served | Ready after | Memory after the queries | First round (cold) | Second round (warm) |"
  echo "|---|---|---|---|---|"
} >> "$REPORT"

ALGO=$(docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE" | sed -n 's/^OSRM_ALGORITHM=//p')
ARGS=(--algorithm "$ALGO" --port 5000 --threads 2)
run "in RAM" "$IMAGE" osrm-routed "${ARGS[@]}" /data/switzerland.osrm
run "mmap" "$IMAGE" osrm-routed "${ARGS[@]}" --mmap --disable-feature-dataset ROUTE_STEPS /data/switzerland.osrm
run "mmap, 256M" --memory 256m --memory-swap 256m "$IMAGE" \
  osrm-routed "${ARGS[@]}" --mmap --disable-feature-dataset ROUTE_STEPS /data/switzerland.osrm
cat "$REPORT"
