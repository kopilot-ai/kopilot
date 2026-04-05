#!/usr/bin/env bash
set -euo pipefail

# Run KubeDevAIOps locally for development (outside the cluster).
# Assumes: Ollama running locally, kubectl configured.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

if [ ! -f .env ]; then
    echo "[INFO] Creating .env from .env.example..."
    cp .env.example .env
fi

if [ ! -d .venv ]; then
    echo "[INFO] Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate 2>/dev/null || . .venv/Scripts/activate 2>/dev/null

echo "[INFO] Installing dependencies..."
pip install -e ".[dev]" --quiet

echo "[INFO] Installing CRDs..."
kubectl apply -f helm/kubedevaiops/crds/ 2>/dev/null || echo "[WARN] Could not install CRDs (cluster not available?)"

echo "[INFO] Starting KubeDevAIOps agent..."
python -m kubedevaiops serve
