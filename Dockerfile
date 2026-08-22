# python:3.13-slim, digest-pinned; refresh with `docker buildx imagetools inspect python:3.13-slim`
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS base

ARG KUBECTL_VERSION=v1.33.4
ARG HELM_VERSION=v3.16.4
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates && \
    ARCH="${TARGETARCH:-$(dpkg --print-architecture)}" && \
    curl -fsSLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl" && \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl.sha256" \
        -o kubectl.sha256 && \
    echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c - && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    curl -fsSLO "https://get.helm.sh/helm-${HELM_VERSION}-linux-${ARCH}.tar.gz" && \
    curl -fsSLO "https://get.helm.sh/helm-${HELM_VERSION}-linux-${ARCH}.tar.gz.sha256sum" && \
    sha256sum -c "helm-${HELM_VERSION}-linux-${ARCH}.tar.gz.sha256sum" && \
    tar -xzf "helm-${HELM_VERSION}-linux-${ARCH}.tar.gz" && \
    install -o root -g root -m 0755 "linux-${ARCH}/helm" /usr/local/bin/helm && \
    apt-get purge -y --auto-remove curl && \
    rm -rf /var/lib/apt/lists/* kubectl kubectl.sha256 helm-* "linux-${ARCH}"

RUN groupadd -r -g 10001 kopilot && \
    useradd -r -u 10001 -g kopilot -d /home/kopilot -m kopilot

WORKDIR /app

COPY requirements-lock.txt ./
# The image's bundled pip mis-resolves the hash-locked set; 26.2.1 is the
# resolver the lock was verified against.
RUN pip install --no-cache-dir pip==26.2.1 && \
    pip install --no-cache-dir --require-hashes -r requirements-lock.txt

COPY pyproject.toml README.md ./
COPY src/ /app/src/
RUN pip install --no-cache-dir --no-deps .

# Numeric UID so Kubernetes runAsNonRoot can verify the user
USER 10001:10001
ENV PYTHONUNBUFFERED=1 \
    HOME=/home/kopilot
EXPOSE 8080

LABEL org.opencontainers.image.title="Kopilot" \
      org.opencontainers.image.description="Approval-gated AI Kubernetes operations agent" \
      org.opencontainers.image.source="https://github.com/kopilot-ai/kopilot" \
      org.opencontainers.image.licenses="Apache-2.0"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8080/health'); assert r.status_code==200"

ENTRYPOINT ["python", "-m", "kopilot"]
CMD ["serve"]
