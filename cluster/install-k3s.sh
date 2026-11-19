#!/usr/bin/env bash
# Simple k3s cluster installer: one master (server) + N workers (agents) over SSH.
#
# Usage:
#   ./install-k3s.sh <master> <worker1> [worker2 ...]
#
# Env vars (optional):
#   SSH_USER      SSH user on all nodes (default: current user); needs passwordless sudo
#   K3S_VERSION   e.g. v1.31.4+k3s1 (default: latest stable)
#
# Example:
#   SSH_USER=ubuntu ./install-k3s.sh 10.0.0.10 10.0.0.11 10.0.0.12

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <master> [worker ...]" >&2
  exit 1
fi

MASTER="$1"; shift
WORKERS=("$@")
SSH_USER="${SSH_USER:-$USER}"
K3S_ENV=""
[ -n "${K3S_VERSION:-}" ] && K3S_ENV="INSTALL_K3S_VERSION=${K3S_VERSION}"

run() { ssh -o StrictHostKeyChecking=accept-new "${SSH_USER}@$1" "$2"; }

echo ">>> Installing k3s server on ${MASTER}"
run "$MASTER" "curl -sfL https://get.k3s.io | sudo ${K3S_ENV} sh -s - server --write-kubeconfig-mode 644 --tls-san ${MASTER}"

echo ">>> Fetching join token"
TOKEN=$(run "$MASTER" "sudo cat /var/lib/rancher/k3s/server/node-token")

for W in "${WORKERS[@]}"; do
  echo ">>> Installing k3s agent on ${W}"
  run "$W" "curl -sfL https://get.k3s.io | sudo ${K3S_ENV} K3S_URL=https://${MASTER}:6443 K3S_TOKEN=${TOKEN} sh -"
done

echo ">>> Saving kubeconfig to ./kubeconfig"
run "$MASTER" "cat /etc/rancher/k3s/k3s.yaml" | sed "s/127.0.0.1/${MASTER}/" > kubeconfig
chmod 600 kubeconfig

echo ">>> Done. Check with:"
echo "    KUBECONFIG=./kubeconfig kubectl get nodes -o wide"
