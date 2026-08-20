FROM python:3.13-slim AS base

ARG KUBECTL_VERSION=v1.31.4
ARG HELM_VERSION=v3.16.4

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates && \
    curl -fsSLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" && \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" \
        -o kubectl.sha256 && \
    echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c - && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    curl -fsSLO "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" && \
    curl -fsSLO "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz.sha256sum" && \
    sha256sum -c "helm-${HELM_VERSION}-linux-amd64.tar.gz.sha256sum" && \
    tar -xzf "helm-${HELM_VERSION}-linux-amd64.tar.gz" && \
    install -o root -g root -m 0755 linux-amd64/helm /usr/local/bin/helm && \
    apt-get purge -y --auto-remove curl && \
    rm -rf /var/lib/apt/lists/* kubectl kubectl.sha256 helm-* linux-amd64

RUN groupadd -r -g 10001 kubedevaiops && \
    useradd -r -u 10001 -g kubedevaiops -d /home/kubedevaiops -m kubedevaiops

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/kubedevaiops/__init__.py src/kubedevaiops/__init__.py
RUN pip install --no-cache-dir .

COPY src/ /app/src/
RUN pip install --no-cache-dir --no-deps .

# Numeric UID so Kubernetes runAsNonRoot can verify the user
USER 10001:10001
ENV PYTHONUNBUFFERED=1 \
    HOME=/home/kubedevaiops
EXPOSE 8080

LABEL org.opencontainers.image.title="Kopilot" \
      org.opencontainers.image.description="Approval-gated AI Kubernetes operations agent" \
      org.opencontainers.image.source="https://github.com/kopilot-ai/kopilot" \
      org.opencontainers.image.licenses="Apache-2.0"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8080/health'); assert r.status_code==200"

ENTRYPOINT ["python", "-m", "kubedevaiops"]
CMD ["serve"]
