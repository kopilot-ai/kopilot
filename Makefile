.PHONY: help install dev lint format helm-lint test run run-operator build build-podman push deploy undeploy deploy-crds local-k8s clean

IMAGE   ?= ghcr.io/kopilot-ai/kopilot
TAG     ?= latest
K8S_NS  ?= kopilot
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

helm-lint: ## Lint the Helm chart
	$(HELM) lint helm/kopilot
	$(HELM) template test helm/kopilot > /dev/null

test: ## Run tests
	pytest tests/ -v --cov=src/kopilot --cov-report=term-missing

run: ## Run the agent locally
	python -m kopilot serve

run-operator: ## Run only the Kopf operator
	python -m kopilot operator

build: ## Build container image
	docker build -t $(IMAGE):$(TAG) .

build-podman: ## Build with Podman
	podman build -t $(IMAGE):$(TAG) .

push: ## Push container image (requires explicit TAG=x.y.z, refuses the "latest" default)
ifeq ($(TAG),latest)
	$(error TAG must be set explicitly, e.g. "make push TAG=0.4.0" -- refusing to push "latest" to $(IMAGE))
endif
	docker push $(IMAGE):$(TAG)

deploy: ## Deploy via Helm
	$(HELM) upgrade --install kopilot helm/kopilot \
		--namespace $(K8S_NS) --create-namespace \
		--set image.repository=$(IMAGE) --set image.tag=$(TAG)

undeploy: ## Remove Helm release
	$(HELM) uninstall kopilot --namespace $(K8S_NS)

deploy-crds: ## Install only CRDs
	$(KUBECTL) apply -f helm/kopilot/crds/

local-k8s: ## Create local Kind cluster for development
	bash scripts/setup-local-k8s.sh

clean: ## Remove build artifacts
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
