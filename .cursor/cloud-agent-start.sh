#!/usr/bin/env bash
set -euo pipefail

if command -v docker >/dev/null && ! sudo docker info >/dev/null 2>&1; then
  sudo dockerd --iptables=false --storage-driver=fuse-overlayfs >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 1 60); do
    if sudo docker info >/dev/null 2>&1; then
      exit 0
    fi
    sleep 1
  done
  echo "dockerd did not become ready; see /tmp/dockerd.log" >&2
  exit 1
fi
