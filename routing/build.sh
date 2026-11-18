#!/usr/bin/env bash
# Builds the routing image: OSRM with car routes for Switzerland, from the
# Geofabrik extract, preprocessed here (it needs a few GB of RAM: run it in
# CI or on a workstation, never on the 1-2 GB app server).
#
#   routing/build.sh <image name, without tag>
#
# Prints the built image (name:tag, the tag being the extract's date and the
# algorithm) on stdout as its last line, and writes a Markdown report of each
# step's time and peak memory to $WORK_DIR/report.md. Needs Docker and curl.
#
# Environment (all optional):
#   ALGORITHM  mld (default; what deploy/compose.yaml serves) or ch
#   PBF_URL    the OSM extract (default: Geofabrik's Switzerland, which also
#              covers Liechtenstein and a few km across the borders)
#   WORK_DIR   scratch directory (default: routing/.work, gitignored)
set -euo pipefail

# The OSRM release the data is built with and served by: both must match.
# Pinned by digest; bump both together (see docs/routing.md).
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:v26.11.0-debian@sha256:e1929ae2818226284d7ef007329098a30851c11528ab3712bc9c8d68da78278b"

IMAGE_NAME=${1:?usage: routing/build.sh <image name>}
ALGORITHM=${ALGORITHM:-mld}
PBF_URL=${PBF_URL:-https://download.geofabrik.de/europe/switzerland-latest.osm.pbf}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK_DIR=${WORK_DIR:-$HERE/.work}
DATA="$WORK_DIR/data"
REPORT="$WORK_DIR/report.md"

case "$ALGORITHM" in mld|ch) ;; *) echo "ALGORITHM must be mld or ch" >&2; exit 1 ;; esac

rm -rf "$DATA"
mkdir -p "$DATA"

# Geofabrik redirects "-latest" to the dated file (switzerland-261117.osm.pbf):
# that date tags the image.
REAL_URL=$(curl -fsSL -o /dev/null -w '%{url_effective}' --head "$PBF_URL")
DATA_DATE=$(basename "$REAL_URL" | grep -oE '[0-9]{6}' | tail -1 || true)
DATA_DATE=${DATA_DATE:-$(date -u +%y%m%d)}
echo "Extract: $REAL_URL" >&2
curl -fsSL --retry 3 -o "$DATA/switzerland.osm.pbf" "$REAL_URL"
EXPECTED_MD5=$(curl -fsSL --retry 3 "$REAL_URL.md5" | cut -d' ' -f1)
echo "$EXPECTED_MD5  $DATA/switzerland.osm.pbf" | md5sum -c - >&2

docker pull --quiet "$OSRM_IMAGE" >&2

{
  echo "### Routing data: $ALGORITHM, extract $DATA_DATE"
  echo
  echo "OSRM image: \`${OSRM_IMAGE%@*}\`, runner: $(nproc) CPUs, $(free -g | awk '/Mem:/ {print $2}') GB RAM, $(uname -m)"
  echo
  echo "Extract: $(basename "$REAL_URL"), $(du -m "$DATA/switzerland.osm.pbf" | cut -f1) MB"
  echo
  echo "| Step | Time | Peak RSS | Peak cgroup memory (incl. page cache) |"
  echo "|---|---|---|---|"
} > "$REPORT"

# Runs one OSRM tool in a container on $DATA and adds its time and peak
# memory to the report: the tool's peak RSS (VmHWM, sampled every second)
# and the container's peak memory as the kernel counts it, page cache
# included (cgroup v2 memory.peak).
step() {
  local name=$1; shift
  local start end out
  start=$(date +%s)
  out=$(docker run --rm -v "$DATA:/data" "$OSRM_IMAGE" bash -c '
    "$@" >&2 &
    pid=$!
    hwm=0
    while kill -0 $pid 2> /dev/null; do
      kb=$(awk "/VmHWM/ {print \$2}" /proc/$pid/status 2> /dev/null || echo 0)
      [ "${kb:-0}" -gt "$hwm" ] && hwm=$kb
      sleep 1
    done
    wait $pid
    echo "$((hwm / 1024)) $(( $(cat /sys/fs/cgroup/memory.peak 2> /dev/null || echo 0) / 1048576 ))"
  ' _ "$@")
  end=$(date +%s)
  read -r rss_mb peak_mb <<< "$out"
  echo "| $name | $((end - start)) s | $rss_mb MB | $peak_mb MB |" >> "$REPORT"
}

step osrm-extract osrm-extract -p /opt/car.lua /data/switzerland.osm.pbf
rm "$DATA/switzerland.osm.pbf"
if [ "$ALGORITHM" = mld ]; then
  step osrm-partition osrm-partition /data/switzerland.osrm
  step osrm-customize osrm-customize /data/switzerland.osrm
else
  step osrm-contract osrm-contract /data/switzerland.osrm
fi

# The tools write some files (.fileIndex) as root with mode 0700: the build
# context, read as the calling user, would copy them as empty files.
docker run --rm -v "$DATA:/data" "$OSRM_IMAGE" chmod -R a+rX /data

TAG="$DATA_DATE-$ALGORITHM"
IMAGE="$IMAGE_NAME:$TAG"
docker build --quiet -t "$IMAGE" -f "$HERE/Dockerfile" \
  --build-arg OSRM_IMAGE="$OSRM_IMAGE" --build-arg ALGORITHM="$ALGORITHM" \
  --label "org.opencontainers.image.source=$REAL_URL" "$DATA" >&2

# The image must hold the files byte for byte.
HOST_SUMS=$(cd "$DATA" && sha256sum -- * | sort)
IMAGE_SUMS=$(docker run --rm -w /data "$IMAGE" sh -c 'sha256sum -- * | sort')
if [ "$HOST_SUMS" != "$IMAGE_SUMS" ]; then
  echo "The image's /data differs from $DATA:" >&2
  diff <(echo "$HOST_SUMS") <(echo "$IMAGE_SUMS") >&2 || true
  exit 1
fi

{
  echo
  echo "Data files: $(du -sm "$DATA" | cut -f1) MB. Image: $(docker image inspect -f '{{.Size}}' "$IMAGE" | awk '{printf "%d", $1 / 1048576}') MB uncompressed."
  echo
  echo '```'
  ls -l "$DATA"
  echo '```'
} >> "$REPORT"
cat "$REPORT" >&2
echo "$IMAGE"
