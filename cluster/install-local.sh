#!/usr/bin/env bash
# Install the services directly (no Argo CD) into a single cluster:
#   1. local-provisioner  (Helm,      base-charts/local-provisioner)
#   2. mlflow             (Helm,      base-charts/mlflow)
#   3. kubeflow-pipelines (Kustomize, base-charts/kubeflow-pipelines/overlays/default)
#
# Release names and namespaces match the ApplicationSets in services/, so Argo CD
# can adopt them later if you switch to the GitOps setup.
#
# Usage:
#   ./install-local.sh [all|local-provisioner|mlflow|kubeflow-pipelines ...]
#
# Env vars (optional):
#   KUBECONFIG      kubeconfig to use (default: ./kubeconfig if present, else kubectl default)
#   HELM_TIMEOUT    how long to wait for each Helm release (default: 10m)
#   KFP_TIMEOUT     how long to wait for the KFP deployments (default: 15m)
#   KFP_OVERLAY     kustomize overlay to apply (default: overlays/default)
#   SKIP_LOCAL_PROVISIONER=1   skip step 1 even if no local-path StorageClass exists
#
# Example:
#   KUBECONFIG=~/.kube/config ./install-local.sh mlflow kubeflow-pipelines

set -euo pipefail

cd "$(dirname "$0")"

if [ -z "${KUBECONFIG:-}" ] && [ -f ./kubeconfig ]; then
  export KUBECONFIG="$PWD/kubeconfig"
fi
HELM_TIMEOUT="${HELM_TIMEOUT:-10m}"
KFP_TIMEOUT="${KFP_TIMEOUT:-15m}"
KFP_OVERLAY="${KFP_OVERLAY:-overlays/default}"

LP_NS="local-provisioner"
MLFLOW_NS="mlflow"
KFP_NS="kubeflow"   # hardcoded by the KFP manifests

for bin in kubectl helm; do
  command -v "$bin" >/dev/null || { echo "Missing required tool: $bin" >&2; exit 1; }
done

# Prefer standalone kustomize, fall back to the one built into kubectl.
if command -v kustomize >/dev/null; then
  KUSTOMIZE=(kustomize build)
else
  KUSTOMIZE=(kubectl kustomize)
fi

echo ">>> Using cluster: $(kubectl config current-context)"
kubectl cluster-info >/dev/null

helm_install() {
  local release="$1" chart="$2" ns="$3"
  echo ">>> Installing ${release} into namespace ${ns} (helm)"
  helm upgrade --install "$release" "$chart" \
    --namespace "$ns" --create-namespace \
    -f "$chart/values.yaml" \
    --wait --timeout "$HELM_TIMEOUT"
}

install_local_provisioner() {
  if [ "${SKIP_LOCAL_PROVISIONER:-0}" = "1" ]; then
    echo ">>> Skipping local-provisioner (SKIP_LOCAL_PROVISIONER=1)"
    return
  fi
  # k3s and kind already ship a local-path provisioner. If a "local-path"
  # StorageClass exists that this Helm release does not own, reuse it instead
  # of failing on the ownership conflict.
  local owner
  owner=$(kubectl get storageclass local-path \
    -o jsonpath='{.metadata.annotations.meta\.helm\.sh/release-name}' 2>/dev/null || echo "__missing__")
  if [ "$owner" != "__missing__" ] && [ "$owner" != "local-provisioner" ]; then
    echo ">>> StorageClass 'local-path' already exists (not managed by this script); skipping local-provisioner"
    return
  fi
  helm_install local-provisioner base-charts/local-provisioner "$LP_NS"
}

install_mlflow() {
  kubectl get storageclass local-path >/dev/null 2>&1 \
    || { echo "StorageClass 'local-path' missing; install local-provisioner first" >&2; exit 1; }
  helm_install mlflow base-charts/mlflow "$MLFLOW_NS"
}

install_kubeflow_pipelines() {
  local dir="base-charts/kubeflow-pipelines/${KFP_OVERLAY}"
  local rendered
  rendered=$(mktemp)
  trap 'rm -f "$rendered"' RETURN

  echo ">>> Rendering ${dir} with ${KUSTOMIZE[*]}"
  "${KUSTOMIZE[@]}" "$dir" > "$rendered"

  echo ">>> Applying kubeflow-pipelines into namespace ${KFP_NS} (kustomize)"
  # Server-side apply: the Argo Workflows CRDs are too big for client-side apply.
  # The first pass may fail on custom resources whose CRDs are not established
  # yet, so wait for the CRDs and apply a second time.
  if ! kubectl apply --server-side --force-conflicts -f "$rendered"; then
    echo ">>> First apply incomplete, waiting for CRDs and retrying"
    kubectl wait --for=condition=Established crd --all --timeout=120s
    kubectl apply --server-side --force-conflicts -f "$rendered"
  fi

  echo ">>> Waiting for kubeflow-pipelines deployments (timeout ${KFP_TIMEOUT})"
  kubectl -n "$KFP_NS" wait --for=condition=Available deployment --all --timeout="$KFP_TIMEOUT"
}

TARGETS=("$@")
[ ${#TARGETS[@]} -eq 0 ] && TARGETS=(all)

for t in "${TARGETS[@]}"; do
  case "$t" in
    all)
      install_local_provisioner
      install_mlflow
      install_kubeflow_pipelines
      ;;
    local-provisioner)  install_local_provisioner ;;
    mlflow)             install_mlflow ;;
    kubeflow-pipelines) install_kubeflow_pipelines ;;
    *) echo "Unknown target: $t (use all|local-provisioner|mlflow|kubeflow-pipelines)" >&2; exit 1 ;;
  esac
done

echo ">>> Done. Access the UIs with:"
echo "    MLflow:             kubectl -n ${MLFLOW_NS} port-forward svc/mlflow 5000:5000"
echo "    Kubeflow Pipelines: kubectl -n ${KFP_NS} port-forward svc/ml-pipeline-ui 8080:80"
