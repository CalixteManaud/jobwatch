SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

IMAGE        ?= jobwatch
TAG          ?= local
SINCE        ?= 26h
ARGS         ?=
KIND_CLUSTER ?= jobwatch
NAMESPACE    ?= jobwatch
CHART        := deploy/helm/jobwatch
KUBE         := kubectl --context kind-$(KIND_CLUSTER)
VERSION      := $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)

# uid/gid de l'utilisateur courant, utilisés par docker-compose.yml
export JOBWATCH_UID := $(shell id -u)
export JOBWATCH_GID := $(shell id -g)

.PHONY: help install lint format test run docker-build docker-run docker-scan helm-lint \
        kind-deploy kind-job kind-delete clean

help: ## Affiche les cibles disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-13s\033[0m %s\n", $$1, $$2}'

install: ## Installe l'environnement (uv) et les hooks pre-commit
	uv sync --frozen
	uvx pre-commit install

lint: ## ruff (lint + format) et mypy --strict
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy

format: ## Reformate et corrige automatiquement
	uv run ruff format src tests
	uv run ruff check --fix src tests

test: ## Tests + couverture (échec sous 80 %)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

run: ## Run local : make run SINCE=7d ARGS="--dry-run"
	uv run jobwatch run --since $(SINCE) $(ARGS)

docker-build: ## Construit l'image Docker (variables IMAGE et TAG)
	docker build --build-arg VERSION=$(VERSION) -t $(IMAGE):$(TAG) .

docker-run: docker-build ## Run dans Docker (compose, .env, volumes state/ et out/)
	@test -f .env || { echo ".env manquant : cp .env.example .env"; exit 1; }
	mkdir -p state out
	docker compose run --rm jobwatch run --since $(SINCE) $(ARGS)

docker-scan: docker-build ## Scan Trivy local de l'image (CRITICAL)
	docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
	  aquasec/trivy:0.76.2 image --severity CRITICAL --ignore-unfixed --exit-code 1 $(IMAGE):$(TAG)

helm-lint: ## Lint du chart Helm
	helm lint --strict $(CHART)
	helm template jobwatch $(CHART) > /dev/null

kind-deploy: docker-build ## Déploie le CronJob sur un cluster kind local (démo)
	@test -f .env || { echo ".env manquant : cp .env.example .env"; exit 1; }
	kind get clusters | grep -qx $(KIND_CLUSTER) \
	  || kind create cluster --name $(KIND_CLUSTER) --config deploy/kind/kind-config.yaml
	kind load docker-image $(IMAGE):$(TAG) --name $(KIND_CLUSTER)
	$(KUBE) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBE) apply -f -
	$(KUBE) -n $(NAMESPACE) create secret generic jobwatch-secrets --from-env-file=.env \
	  --dry-run=client -o yaml | $(KUBE) apply -f -
	helm upgrade --install jobwatch $(CHART) --kube-context kind-$(KIND_CLUSTER) \
	  -n $(NAMESPACE) -f deploy/kind/values-kind.yaml \
	  --set image.repository=$(IMAGE) --set image.tag=$(TAG) --wait
	$(KUBE) -n $(NAMESPACE) get cronjob,pvc

kind-job: ## Lance immédiatement un run du CronJob sur kind et suit ses logs
	@job=jobwatch-manual-$$(date +%s); \
	$(KUBE) -n $(NAMESPACE) create job $$job --from=cronjob/jobwatch; \
	$(KUBE) -n $(NAMESPACE) wait --for=condition=Ready pod -l job-name=$$job --timeout=120s || true; \
	$(KUBE) -n $(NAMESPACE) logs -f job/$$job

kind-delete: ## Supprime le cluster kind
	kind delete cluster --name $(KIND_CLUSTER)

clean: ## Supprime caches et rapports
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov dist
