#!/usr/bin/env bash
# Bootstrap the cluster: install the base services (Vault, Argo CD) with Helm,
# register the GitOps repo in Argo CD, then apply main.yaml (app-of-apps).
# After that Argo CD takes over and manages itself, Vault and everything in services/.
#
# Usage:
#   ./install-services.sh
#
# Env vars (optional):
#   KUBECONFIG     kubeconfig to use (default: ./kubeconfig if present, else kubectl default)
#   REPO_SSH_KEY   private SSH key with read access to the GitOps repo
#                  (default: ~/.ssh/id_ed25519, falling back to ~/.ssh/id_rsa)
#   HELM_TIMEOUT   how long to wait for each Helm release (default: 10m)
#
# Example:
#   REPO_SSH_KEY=~/.ssh/deploy_key ./install-services.sh

set -euo pipefail

cd "$(dirname "$0")"

if [ -z "${KUBECONFIG:-}" ] && [ -f ./kubeconfig ]; then
  export KUBECONFIG="$PWD/kubeconfig"
fi
HELM_TIMEOUT="${HELM_TIMEOUT:-10m}"

# Release names and namespaces must match the Argo CD Applications in main.yaml,
# so Argo CD adopts these releases instead of creating duplicates.
ARGOCD_NS="argo-cd"
VAULT_NS="vault"
REPO_URL=$(awk '/repoURL:/ {print $2; exit}' main.yaml)

for bin in kubectl helm; do
  command -v "$bin" >/dev/null || { echo "Missing required tool: $bin" >&2; exit 1; }
done

if [ -z "${REPO_SSH_KEY:-}" ]; then
  for k in ~/.ssh/id_ed25519 ~/.ssh/id_rsa; do
    [ -f "$k" ] && { REPO_SSH_KEY="$k"; break; }
  done
fi
if [ -z "${REPO_SSH_KEY:-}" ] || [ ! -f "$REPO_SSH_KEY" ]; then
  echo "No SSH key found for ${REPO_URL}; set REPO_SSH_KEY=/path/to/key" >&2
  exit 1
fi

echo ">>> Using cluster: $(kubectl config current-context)"
kubectl cluster-info >/dev/null

helm_install() {
  local release="$1" chart="$2" ns="$3"
  echo ">>> Installing ${release} into namespace ${ns}"
  # Use the vendored charts/*.tgz; only fetch dependencies if they are missing.
  helm dependency build "$chart" >/dev/null 2>&1 || helm dependency update "$chart"
  helm upgrade --install "$release" "$chart" \
    --namespace "$ns" --create-namespace \
    -f "$chart/values.yaml" \
    --wait --timeout "$HELM_TIMEOUT"
}

# 1. Vault first: the Argo CD repo-server sidecars (argocd-vault-plugin) talk to it.
helm_install vault base-charts/vault "$VAULT_NS"

# 2. Argo CD (also installs the Application/ApplicationSet CRDs).
helm_install argo-cd base-charts/argo-cd "$ARGOCD_NS"

# 3. Give Argo CD access to the GitOps repo.
echo ">>> Registering repository ${REPO_URL}"
kubectl -n "$ARGOCD_NS" create secret generic gitops-repo \
  --from-literal=type=git \
  --from-literal=url="$REPO_URL" \
  --from-file=sshPrivateKey="$REPO_SSH_KEY" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - argocd.argoproj.io/secret-type=repository -o yaml \
  | kubectl apply -f -

# 4. Main service: app-of-apps that points Argo CD at the repo.
echo ">>> Applying main.yaml"
kubectl apply -n "$ARGOCD_NS" -f main.yaml

echo ">>> Done. Watch the sync with:"
echo "    kubectl -n ${ARGOCD_NS} get applications,applicationsets"
echo ">>> Argo CD UI:"
echo "    kubectl -n ${ARGOCD_NS} port-forward svc/argo-cd-argocd-server 8080:80"
echo "    password: kubectl -n ${ARGOCD_NS} get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
