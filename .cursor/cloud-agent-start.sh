#!/usr/bin/env bash
set -euo pipefail

if command -v docker >/dev/null && ! sudo docker info >/dev/null 2>&1; then
  storage_driver=fuse-overlayfs
  if ! command -v fuse-overlayfs >/dev/null; then
    storage_driver=vfs
  fi
  sudo dockerd --iptables=false --storage-driver="${storage_driver}" >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 1 60); do
    if sudo docker info >/dev/null 2>&1; then
      exit 0
    fi
    sleep 1
  done
  echo "dockerd did not become ready; see /tmp/dockerd.log" >&2
  exit 1
fi
