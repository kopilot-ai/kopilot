.PHONY: help install dev lint test run build push deploy local-k8s clean

IMAGE   ?= kubedevaiops/kubedevaiops
TAG     ?= latest
K8S_NS  ?= kubedevaiops
HELM    ?= helm
KUBECTL ?= kubectl

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install the package
	pip install .

dev: ## Install with dev dependencies (editable)
	pip install -e ".[dev]"

lint: ## Run linter
	ruff check src/ tests/
	ruff format --check src/ tests/

format: ## Auto-format code
	ruff check --fix src/ tests/
	ruff format src/ tests/

test: ## Run tests
	pytest tests/ -v --cov=src/kubedevaiops --cov-report=term-missing

run: ## Run the agent locally
	python -m kubedevaiops serve

run-operator: ## Run only the Kopf operator
	python -m kubedevaiops operator

build: ## Build container image
	docker build -t $(IMAGE):$(TAG) .

build-podman: ## Build with Podman
	podman build -t $(IMAGE):$(TAG) .

push: ## Push container image
	docker push $(IMAGE):$(TAG)

deploy: ## Deploy via Helm
	$(HELM) upgrade --install kubedevaiops helm/kubedevaiops \
		--namespace $(K8S_NS) --create-namespace \
		--set image.repository=$(IMAGE) --set image.tag=$(TAG)

undeploy: ## Remove Helm release
	$(HELM) uninstall kubedevaiops --namespace $(K8S_NS)

deploy-crds: ## Install only CRDs
	$(KUBECTL) apply -f helm/kubedevaiops/crds/

local-k8s: ## Create local Kind cluster for development
	bash scripts/setup-local-k8s.sh

clean: ## Remove build artifacts
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
