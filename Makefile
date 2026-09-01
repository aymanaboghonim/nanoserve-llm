# NanoServe Makefile — idempotent developer targets.
#
# Every target is safe to re-run (apply/bootstrap/verify are idempotent).
#   make verify   # full Phase-2 verification gate
#   make apply    # apply all Phase-2b manifests (two-tier, quota, otel)
#   make secret   # render the DeepSeek Secret from gitignored .env
#   make bench    # streaming TTFT/ITL benchmark (needs live endpoint)
#   make trace    # OTel span emitter (needs live endpoint + collector)
#   make argo     # install Argo CD (full, non-HA) + bootstrap app-of-apps
#   make check    # pre-commit gate (lint/format/type/test/shellcheck)

SHELL := /bin/bash
KUBECTL ?= kubectl
PATH := $(HOME)/.local/bin:$(PATH)

.PHONY: install-clis bootstrap verify apply secret bench trace argo check clean

install-clis:           ## Install kubectl/helm/yq into ~/.local/bin (no root)
	bash infra/install-clis.sh

bootstrap:              ## Host bootstrap: NVIDIA toolkit + k3s (root; needs sudo)
	sudo bash infra/bootstrap.sh

verify:                 ## Run the full Phase-2 verification gate (exit 0 = green)
	bash infra/verify.sh

apply:                  ## Apply all Phase-2b manifests (idempotent)
	$(KUBECTL) apply -k manifests/infra
	$(KUBECTL) apply -f manifests/serving/secret-deepseek.yaml
	$(KUBECTL) apply -f manifests/serving/backend-deepseek.yaml
	$(KUBECTL) apply -f manifests/serving/aiservicebackend-deepseek.yaml
	$(KUBECTL) apply -f manifests/serving/aigatewayroute.yaml
	$(KUBECTL) apply -f manifests/serving/quotapolicy.yaml
	$(KUBECTL) apply -f manifests/serving/backendtrafficpolicy.yaml
	$(KUBECTL) apply -f manifests/serving/request-mirror-shadow.yaml
	$(KUBECTL) apply -f manifests/serving/otel-collector.yaml
	@echo "✔ Phase-2b manifests applied"

secret:                 ## Render the DeepSeek Secret from .env (gitignored)
	@test -f .env || { echo "error: .env missing (copy .env.example, set DEEPSEEK_API_KEY)"; exit 1; }
	@test -n "$$DEEPSEEK_API_KEY" || { echo "error: DEEPSEEK_API_KEY not set in .env"; exit 1; }
	@sed 's/CHANGE_ME/'"$$DEEPSEEK_API_KEY"'/' manifests/serving/secret-deepseek.yaml | $(KUBECTL) apply -f -
	@echo "✔ deepseek Secret applied (key not logged)"

bench:                  ## Streaming TTFT/ITL benchmark (needs live endpoint)
	uv run --group dev python scripts/latency_benchmark.py $(BENCH_ARGS)

trace:                  ## Emit OTel spans to the collector (needs live endpoint)
	uv run --group dev python scripts/tracer.py $(TRACE_ARGS)

argo:                   ## Install Argo CD (full, non-HA) + bootstrap app-of-apps
	bash argo/install.sh

check:                  ## Run the full pre-commit gate
	uv run pre-commit run --all-files

clean:                  ## Remove local caches/artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .locust
