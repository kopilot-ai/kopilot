#!/usr/bin/env bash
set -euo pipefail

# Setup a local Kubernetes cluster for KubeDevAIOps development.
# Supports: kind (preferred), k3d, minikube, or podman+kind.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CLUSTER_NAME="${CLUSTER_NAME:-kubedevaiops-dev}"

info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ── Detect container runtime ──────────────────────────────────────────────

detect_runtime() {
    if command -v podman &>/dev/null; then
        export KIND_EXPERIMENTAL_PROVIDER=podman
        info "Using Podman as container runtime"
    elif command -v docker &>/dev/null; then
        info "Using Docker as container runtime"
    else
        error "Neither Docker nor Podman found. Install one first."
    fi
}

# ── Install kind if missing ───────────────────────────────────────────────

ensure_kind() {
    if command -v kind &>/dev/null; then
        info "kind already installed: $(kind version)"
        return
    fi
    info "Installing kind..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.25.0/kind-linux-amd64
        chmod +x ./kind
        sudo mv ./kind /usr/local/bin/kind
    elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        curl -Lo kind-windows-amd64.exe https://kind.sigs.k8s.io/dl/v0.25.0/kind-windows-amd64
        mv kind-windows-amd64.exe /usr/local/bin/kind.exe
    else
        brew install kind 2>/dev/null || error "Cannot install kind automatically on this OS"
    fi
}

# ── Create cluster ────────────────────────────────────────────────────────

create_cluster() {
    if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
        info "Cluster '${CLUSTER_NAME}' already exists"
        kind export kubeconfig --name "${CLUSTER_NAME}"
        return
    fi
    info "Creating Kind cluster '${CLUSTER_NAME}'..."
    kind create cluster \
        --name "${CLUSTER_NAME}" \
        --config "${PROJECT_DIR}/deploy/kind-config.yaml" \
        --wait 120s
    info "Cluster created successfully"
}

# ── Install CRDs ──────────────────────────────────────────────────────────

install_crds() {
    info "Installing KubeDevAIOps CRDs..."
    kubectl apply -f "${PROJECT_DIR}/helm/kubedevaiops/crds/"
    info "CRDs installed"
}

# ── Install metrics-server (optional) ────────────────────────────────────

install_metrics_server() {
    if kubectl get deployment metrics-server -n kube-system &>/dev/null; then
        info "metrics-server already installed"
        return
    fi
    info "Installing metrics-server..."
    kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
    kubectl patch deployment metrics-server -n kube-system --type=json \
        -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
    info "metrics-server installed (with --kubelet-insecure-tls for local dev)"
}

# ── Main ──────────────────────────────────────────────────────────────────

main() {
    info "=== KubeDevAIOps Local K8s Setup ==="
    detect_runtime
    ensure_kind
    create_cluster
    install_crds
    install_metrics_server
    info ""
    info "=== Setup complete ==="
    info "Cluster: ${CLUSTER_NAME}"
    info "Context: kind-${CLUSTER_NAME}"
    info ""
    info "Next steps:"
    info "  1. Build image:  make build-podman  (or make build)"
    info "  2. Load image:   kind load docker-image kubedevaiops/kubedevaiops:latest --name ${CLUSTER_NAME}"
    info "  3. Deploy:       kubectl apply -f deploy/quickstart.yaml"
    info "  4. Or use Helm:  make deploy"
}

main "$@"
