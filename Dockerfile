FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git ca-certificates && \
    curl -LO "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash && \
    apt-get purge -y --auto-remove curl && \
    rm -rf /var/lib/apt/lists/* kubectl

RUN groupadd -r kubedevaiops && useradd -r -g kubedevaiops -d /app kubedevaiops

WORKDIR /app

COPY pyproject.toml .
COPY src/kubedevaiops/__init__.py src/kubedevaiops/__init__.py
RUN pip install --no-cache-dir .

COPY src/ /app/src/
RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p /home/kubedevaiops/.kubedevaiops && \
    chown -R kubedevaiops:kubedevaiops /home/kubedevaiops

USER kubedevaiops
ENV PYTHONUNBUFFERED=1
EXPOSE 8080 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8080/health'); assert r.status_code==200"

ENTRYPOINT ["python", "-m", "kubedevaiops"]
CMD ["serve"]
