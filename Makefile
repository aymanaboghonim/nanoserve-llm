# NanoServe Makefile — idempotent developer targets.
#
# Every target is safe to re-run (apply/bootstrap/verify are idempotent).
#   make verify   # full verification gate (Phase 2/2b/3, incl. KServe + Argo)
#   make apply    # apply serving-plane manifests (two-tier, quota, otel, KServe)
#   make model    # fetch model weights host-side (Modelscope; HF throttled)
#   make loadpvc  # copy cached model weights into the nanoserve-model-cache PVC
#   make secret   # render the DeepSeek Secret from gitignored .env
#   make serve    # status of the LLMInferenceService / vLLM pod
#   make bench    # streaming TTFT/ITL benchmark (needs live endpoint)
#   make trace    # OTel span emitter (needs live endpoint + collector)
#   make argo     # install Argo CD (full, non-HA)
#   make check    # pre-commit gate (lint/format/type/test/shellcheck)

SHELL := /bin/bash
KUBECTL ?= kubectl
PATH := $(HOME)/.local/bin:$(PATH)

.PHONY: install-clis bootstrap verify apply model loadpvc secret serve bench trace argo check clean

install-clis:           ## Install kubectl/helm/yq into ~/.local/bin (no root)
	bash infra/install-clis.sh

bootstrap:              ## Host bootstrap: NVIDIA toolkit + k3s (root; needs sudo)
	sudo bash infra/bootstrap.sh

verify:                 ## Run the full verification gate (exit 0 = green)
	bash infra/verify.sh

apply:                  ## Apply serving-plane manifests (idempotent)
	$(KUBECTL) apply -f manifests/serving/model-pvc.yaml
	$(KUBECTL) apply -f manifests/serving/llm-d-epp.yaml
	$(KUBECTL) apply -f manifests/serving/inferencepool.yaml
	$(KUBECTL) apply -f manifests/serving/llminferenceservice.yaml
	$(KUBECTL) apply -f manifests/serving/backend-deepseek.yaml
	$(KUBECTL) apply -f manifests/serving/aiservicebackend-deepseek.yaml
	$(KUBECTL) apply -f manifests/serving/aigatewayroute.yaml
	$(KUBECTL) apply -f manifests/serving/quotapolicy.yaml
	$(KUBECTL) apply -f manifests/serving/backendtrafficpolicy.yaml
	$(KUBECTL) apply -f manifests/serving/request-mirror-shadow.yaml
	$(KUBECTL) apply -f manifests/serving/otel-collector.yaml
	@echo "✔ serving-plane manifests applied"

model:                  ## Fetch model weights host-side (Modelscope; resumable)
	bash infra/fetch-model.sh

loadpvc:                ## Copy cached model weights into the model PVC
	bash infra/load-model-pvc.sh

serve:                  ## Status of the LLMInferenceService + vLLM pod
	@$(KUBECTL) get llmisvc -n kserve 2>/dev/null || echo "(no LLMInferenceService yet — run: make apply)"
	@$(KUBECTL) get pods -n kserve -l serving.kserve.io/inferenceservice=qwen-llm -o wide 2>/dev/null || true
	@$(KUBECTL) get pods -n kserve -l app=llm-d-epp 2>/dev/null || true

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
