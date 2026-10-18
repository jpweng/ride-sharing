#!/usr/bin/env bash
# Runs on the app server as root, via SSM Run Command from CI, in
# /opt/ride-sharing next to compose.yaml and .env.
set -euo pipefail

# SSM doesn't set HOME; Docker needs it to find the ECR credential helper
# config in /root/.docker.
export HOME=/root
cd "$(dirname "$0")"

# On a freshly created server, wait for first-boot setup (Docker, /data).
cloud-init status --wait > /dev/null || true
mountpoint -q /data || { echo "/data (EBS data volume) is not mounted" >&2; exit 1; }

docker compose pull --quiet
# Recreates only containers whose image changed, then blocks until the app's
# healthcheck passes; fails the deploy otherwise.
docker compose up -d --remove-orphans --wait --wait-timeout 120
docker image prune -af > /dev/null
docker compose ps
