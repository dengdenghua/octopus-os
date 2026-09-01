.PHONY: install test test-fast production-readiness production-readiness-static lint lint-ruff format fix clean security \
        agent-bundle agent-bundle-local agent-bundle-verify appliance-build \
        omv-host-bundle \
        dev \
        up up-full down logs restart ps rebuild \
        k8s-apply k8s-delete k8s-status \
        frontend-install frontend-dev frontend-build frontend-clean frontend-typecheck

# Development convenience: production operators persist this value in appliance.env.
# One make invocation reuses the same generated value for both trusted containers.
DOCKER_COMPOSE_BASE = ECHO_DOCKER_PROXY_TOKEN=$${ECHO_DOCKER_PROXY_TOKEN:-$$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')} docker compose

# ─── Install ─────────────────────────────────────────
install:  ## Install the unified Echo OS + Agent development environment
	pip install -e ".[dev,serve,tracing,web,local-auth]"

# ─── Test ────────────────────────────────────────────
test:  ## Run pytest with coverage
	pytest --cov=appliance -v

test-fast:  ## Run pytest without coverage
	pytest -q

production-readiness:  ## Run the production readiness gate with isolated runtime state
	@mkdir -p $${ECHO_READINESS_DATA_DIR:-test-results/production-readiness/data}
	ECHO_HOME=$${ECHO_READINESS_HOME:-test-results/production-readiness} \
	ECHO_DATA_DIR=$${ECHO_READINESS_DATA_DIR:-test-results/production-readiness/data} \
	$${PYTHON:-$$(if [ -x .venv/bin/python ]; then printf '%s' .venv/bin/python; else printf '%s' python; fi)} -m scripts.production_readiness_gate \
		--review-queue-path "$${ECHO_READINESS_REVIEW_QUEUE:-test-results/production-readiness/data/review_queue.json}" \
		--json-output "$${ECHO_READINESS_REPORT:-test-results/production-readiness/readiness_gate.json}"

production-readiness-static:  ## Deterministic checks; explicitly not release proof
	@mkdir -p $${ECHO_READINESS_DATA_DIR:-test-results/production-readiness/data}
	ECHO_HOME=$${ECHO_READINESS_HOME:-test-results/production-readiness} \
	ECHO_DATA_DIR=$${ECHO_READINESS_DATA_DIR:-test-results/production-readiness/data} \
	$${PYTHON:-$$(if [ -x .venv/bin/python ]; then printf '%s' .venv/bin/python; else printf '%s' python; fi)} -m scripts.production_readiness_gate \
		--static-only \
		--review-queue-path "$${ECHO_READINESS_REVIEW_QUEUE:-test-results/production-readiness/data/review_queue.json}" \
		--json-output "$${ECHO_READINESS_REPORT:-test-results/production-readiness/readiness_gate.json}"

# ─── Lint ────────────────────────────────────────────
lint: lint-ruff  ## Run all linters

lint-ruff:  ## Run ruff
	ruff check appliance/ tests/appliance/ deploy/appliance/agent_bundle.py deploy/appliance/dependency_lock.py deploy/appliance/image_release.py deploy/appliance/verify-running-appliance.py deploy/omv/echo_omv_host.py deploy/omv/host_bundle.py deploy/omv/real_omv_nfs_probe.py deploy/omv/verify-real-omv-x86-evidence.py
	ruff format --check appliance/ tests/appliance/ deploy/appliance/agent_bundle.py deploy/appliance/dependency_lock.py deploy/appliance/image_release.py deploy/appliance/verify-running-appliance.py deploy/omv/echo_omv_host.py deploy/omv/host_bundle.py deploy/omv/real_omv_nfs_probe.py deploy/omv/verify-real-omv-x86-evidence.py

format:  ## Run ruff format
	ruff format appliance/ tests/appliance/ deploy/appliance/agent_bundle.py deploy/appliance/dependency_lock.py deploy/appliance/image_release.py deploy/appliance/verify-running-appliance.py deploy/omv/echo_omv_host.py deploy/omv/host_bundle.py deploy/omv/real_omv_nfs_probe.py deploy/omv/verify-real-omv-x86-evidence.py

fix:  ## Run ruff fixes and formatting
	ruff check --fix appliance/ tests/appliance/ deploy/appliance/agent_bundle.py deploy/appliance/dependency_lock.py deploy/appliance/image_release.py deploy/appliance/verify-running-appliance.py deploy/omv/echo_omv_host.py deploy/omv/host_bundle.py deploy/omv/real_omv_nfs_probe.py deploy/omv/verify-real-omv-x86-evidence.py
	ruff format appliance/ tests/appliance/ deploy/appliance/agent_bundle.py deploy/appliance/dependency_lock.py deploy/appliance/image_release.py deploy/appliance/verify-running-appliance.py deploy/omv/echo_omv_host.py deploy/omv/host_bundle.py deploy/omv/real_omv_nfs_probe.py deploy/omv/verify-real-omv-x86-evidence.py

security:  ## Run security scans (bandit + pip-audit)
	bandit -r appliance/ deploy/appliance/dependency_lock.py deploy/appliance/image_release.py deploy/appliance/verify-running-appliance.py deploy/omv/echo_omv_host.py deploy/omv/host_bundle.py deploy/omv/real_omv_nfs_probe.py deploy/omv/verify-real-omv-x86-evidence.py -ll -ii
	pip-audit

# ─── Clean ───────────────────────────────────────────
clean:  ## Clean caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf build/ dist/ *.egg-info .coverage htmlcov/

# ─── Docker ──────────────────────────────────────────
agent-bundle:  ## Build release-grade Echo wheel/resources/Codex from this repository
	./deploy/appliance/prepare-agent-bundle.sh

agent-bundle-local:  ## Build a clearly marked bundle from the dirty local checkout
	ECHO_AGENT_ALLOW_DIRTY=1 ./deploy/appliance/prepare-agent-bundle.sh

agent-bundle-verify:  ## Verify all prepared Agent artifacts against their manifest
	./deploy/appliance/bundle-python.sh deploy/appliance/agent_bundle.py verify \
		--bundle-root deploy/appliance \
		--manifest deploy/appliance/agent-bundle.json

appliance-build: agent-bundle  ## Prepare the bundle and build the Echo OS image
	docker build -t echo-os .

omv-host-bundle:  ## Build and self-verify the architecture-neutral OMV host bundle
	python deploy/omv/host_bundle.py build --output-directory dist

up: agent-bundle-verify  ## Start the verified Echo + least-privilege control stack
	@test -f config.yaml || cp config.example.yaml config.yaml
	@test -f .env || cp .env.example .env
	@mkdir -p data storage
	PUID=$$(id -u) PGID=$$(id -g) $(DOCKER_COMPOSE_BASE) up -d --build
	@echo "→ http://localhost:8000/  ·  logs: make logs"

up-full:  ## Start the full compose stack
	@test -f config.yaml || cp config.example.yaml config.yaml
	@test -f .env || cp .env.example .env
	@mkdir -p data data/redis data/grafana
	docker compose -f docker-compose.full.yml up -d

down:  ## Stop and remove containers while keeping ./data
	-$(DOCKER_COMPOSE_BASE) down
	-docker compose -f docker-compose.full.yml down

logs:  ## Tail appliance logs
	$(DOCKER_COMPOSE_BASE) logs -f echo-os

restart:  ## Restart the appliance container after config changes
	$(DOCKER_COMPOSE_BASE) restart echo-os

ps:  ## Show compose process status
	$(DOCKER_COMPOSE_BASE) ps

rebuild: agent-bundle  ## Rebuild image and restart after code changes
	$(DOCKER_COMPOSE_BASE) build --no-cache echo-os
	PUID=$$(id -u) PGID=$$(id -g) $(DOCKER_COMPOSE_BASE) up -d echo-os

# ─── Kubernetes ──────────────────────────────────────
k8s-apply:  ## Apply deploy/k8s with kustomize
	@if ! grep -Eq '^[[:space:]]*digest:[[:space:]]*sha256:[0-9a-fA-F]{64}[[:space:]]*$$' deploy/k8s/kustomization.yaml || grep -Eq '^[[:space:]]*digest:[[:space:]]*sha256:0{64}[[:space:]]*$$' deploy/k8s/kustomization.yaml; then echo "ERROR: set images[].digest in deploy/k8s/kustomization.yaml to a non-zero cosign-verified release digest"; exit 1; fi
	kubectl apply -k deploy/k8s/

k8s-delete:  ## Delete k8s resources; namespace PVCs may need manual cleanup
	kubectl delete -k deploy/k8s/

k8s-status:  ## Show namespace resources
	kubectl -n echo-agent get all,pvc,cm,secret

# ─── Frontend · Vite + React ─────────────────────────
frontend-install:  ## Install frontend dependencies
	cd frontend && corepack enable && pnpm install --frozen-lockfile

frontend-dev:  ## Run the frontend dev server
	cd frontend && pnpm dev

frontend-build:  ## Build frontend/dist for FastAPI /ui mounting
	cd frontend && pnpm build

frontend-typecheck:  ## Run TypeScript type checking
	cd frontend && pnpm typecheck

frontend-clean:  ## Clean frontend build outputs
	rm -rf frontend/dist frontend/.vite frontend/node_modules/.vite

# ─── Utilities ───────────────────────────────────────
tree:  ## Show a compact tracked-file tree
	@git ls-files | xargs -I {} echo {} | head -100

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.DEFAULT_GOAL := help
