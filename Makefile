# NanoServe Makefile — idempotent developer targets.
#
# Every target is safe to re-run (apply/bootstrap/verify are idempotent).
#   make verify        # full verification gate (Phase 2/2b/3/4, incl. KServe)
#   make apply         # apply serving-plane manifests (two-tier, quota, KServe)
#   make download-model # fetch model weights host-side -> HF cache + flat dir
#   make secret        # render the Gemini Secret from gitignored .env
#   make serve         # status of the LLMInferenceService / vLLM pod
#   make test-unit     # hermetic unit + mocked contract tests (no cluster)
#   make test-e2e      # live Repo-2 contract suites (needs cluster + /healthz)
#   make bench         # streaming TTFT/ITL benchmark (defaults to serving gateway)
#   make eval          # DeepEval contract + quality suite via the serving gateway
#   make load          # Locust smoke via the serving gateway (8u/1/s/2m)
#   make trace         # OTel span emitter (defaults to serving gateway + collector)
#   make telemetry     # apply Phase 4 Prometheus + Grafana manifests
#   make secret-grafana  # apply the Grafana admin Secret from gitignored .env
#   make argo          # install Argo CD (full, non-HA)
#   make check         # pre-commit gate (lint/format/type/test/shellcheck)

SHELL := /bin/bash
KUBECTL ?= kubectl
PATH := $(HOME)/.local/bin:$(PATH)

.PHONY: install-clis bootstrap verify apply download-model secret serve test-unit test-e2e contract-test bench eval load stress trace telemetry secret-grafana argo check clean

install-clis:           ## Install kubectl/helm/yq into ~/.local/bin (no root)
	bash infra/install-clis.sh

bootstrap:              ## Host bootstrap: NVIDIA toolkit + k3s (root; needs sudo)
	sudo bash infra/bootstrap.sh

verify:                 ## Run the full verification gate (exit 0 = green)
	bash infra/verify.sh

apply:                  ## Apply serving-plane manifests (idempotent)
	$(KUBECTL) apply -f manifests/serving/model-pv.yaml
	$(KUBECTL) apply -f manifests/serving/model-pvc.yaml
	$(KUBECTL) apply -f manifests/serving/llm-d-epp.yaml
	$(KUBECTL) apply -f manifests/serving/inferencepool.yaml
	$(KUBECTL) apply -f manifests/serving/llminferenceservice.yaml
	$(KUBECTL) apply -f manifests/serving/backend-gemini.yaml
	$(KUBECTL) apply -f manifests/serving/backendsecuritypolicy-gemini.yaml
	$(KUBECTL) apply -f manifests/serving/aiservicebackend-gemini.yaml
	$(KUBECTL) apply -f manifests/serving/aigatewayroute.yaml
	$(KUBECTL) apply -f manifests/serving/quotapolicy.yaml
	$(KUBECTL) apply -f manifests/serving/backendtrafficpolicy-local.yaml
	$(KUBECTL) apply -f manifests/serving/cors-policy.yaml
	$(KUBECTL) apply -f manifests/serving/healthz-route.yaml
	# request-mirror-shadow.yaml disabled (overlaps the LLMISVC route) — see header.
	$(KUBECTL) apply -f manifests/serving/otel-collector.yaml
	@echo "✔ serving-plane manifests applied"

download-model:         ## Download model weights host-side (HF cache -> flat dir)
	bash scripts/download_model.sh

secret:                 ## Render the Gemini Secret from .env (gitignored)
	@test -f .env || { echo "error: .env missing (copy .env.example, set GEMINI_API_KEY)"; exit 1; }
	@test -n "$$GEMINI_API_KEY" || { echo "error: GEMINI_API_KEY not set in .env"; exit 1; }
	@sed 's/CHANGE_ME/'"$$GEMINI_API_KEY"'/' manifests/serving/secret-gemini.yaml | $(KUBECTL) apply -f -
	@echo "✔ gemini Secret applied (key not logged)"

serve:                  ## Status of the LLMInferenceService + vLLM pod
	@$(KUBECTL) get llmisvc -n kserve 2>/dev/null || echo "(no LLMInferenceService yet — run: make apply)"
	@$(KUBECTL) get pods -n kserve -l serving.kserve.io/inferenceservice=granite-llm -o wide 2>/dev/null || true
	@$(KUBECTL) get pods -n kserve -l app=llm-d-epp 2>/dev/null || true

test-unit:              ## Hermetic unit + mocked contract tests (no cluster, no network)
	uv run pytest tests -q

test-e2e:               ## Live Repo-2 contract suites (gate on /healthz first)
	@GW=$$($(KUBECTL) get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}' 2>/dev/null); \
	test -n "$$GW" || { echo "error: kserve-gateway has no address (is it Programmed?)"; exit 1; }; \
	echo "==> probing http://localhost:8000/healthz ..."; \
	curl -sSf --connect-timeout 2 --max-time 5 http://localhost:8000/healthz >/dev/null \
	  || { echo "error: gateway /healthz not reachable on localhost:8000 (Repo-2 contract)"; exit 1; }; \
	echo "==> e2e via http://localhost:8000/v1 (NANOSERVE_E2E_LIVE=1)"; \
	NANOSERVE_E2E_LIVE=1 uv run pytest tests/e2e -v

contract-test:          ## Portable Repo-2 conformance suite (decoupled; env-driven)
	@GW=$$($(KUBECTL) get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}' 2>/dev/null); \
	test -n "$$GW" || { echo "error: kserve-gateway has no address (is it Programmed?)"; exit 1; }; \
	echo "==> conformance via http://$$GW/v1 (NANOSERVE_CONTRACT_LIVE=1)"; \
	NANOSERVE_CONTRACT_LIVE=1 NANOSERVE_BASE_URL="http://$$GW/v1" \
	  uv run pytest contract_tests -v $(CONTRACT_ARGS)

telemetry:              ## Apply Phase 4 telemetry manifests (ServiceMonitors + Grafana)
	make prometheus-operator
	$(KUBECTL) apply -k manifests/telemetry
	@echo "✔ telemetry manifests applied (operator + ServiceMonitors + Grafana)"
	@echo "  next: make secret-grafana  (admin Secret from .env)"
	@echo "  UI:   http://grafana.192.168.1.2.nip.io"

prometheus-operator:    ## Install the Prometheus Operator (minimal helm values)
	helm upgrade --install prometheus-operator prometheus-community/kube-prometheus-stack \
	  -n monitoring --create-namespace \
	  -f manifests/telemetry/prometheus-operator-values.yaml
	@echo "✔ Prometheus Operator installed (managed Prometheus reconciles ServiceMonitors)"

secret-grafana:         ## Render the Grafana admin Secret from .env (gitignored)
	@test -f .env || { echo "error: .env missing (copy .env.example, set GRAFANA_ADMIN_PASSWORD)"; exit 1; }
	@test -n "$$GRAFANA_ADMIN_PASSWORD" || { echo "error: GRAFANA_ADMIN_PASSWORD not set in .env"; exit 1; }
	@sed 's/CHANGE_ME/'"$$GRAFANA_ADMIN_PASSWORD"'/' manifests/telemetry/grafana-secret.yaml | $(KUBECTL) apply -f -
	@echo "✔ grafana-admin Secret applied (password not logged)"

bench:                  ## Streaming TTFT/ITL benchmark (defaults to serving gateway)
	@GW=$$($(KUBECTL) get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}' 2>/dev/null); \
	if [ -n "$$GW" ] && ! echo '$(BENCH_ARGS)' | grep -q -- '--url'; then \
	  export NANOSERVE_BASE_URL="http://$$GW/v1"; echo "==> bench via http://$$GW/v1"; \
	fi; \
	uv run --group dev python scripts/latency_benchmark.py $(BENCH_ARGS)

eval:                   ## DeepEval contract + quality suite via the serving gateway
	@GW=$$($(KUBECTL) get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}' 2>/dev/null); \
	test -n "$$GW" || { echo "error: kserve-gateway has no address (is it Programmed?)"; exit 1; }; \
	echo "==> evals via http://$$GW/v1 (NANOSERVE_EVAL_LIVE=1)"; \
	NANOSERVE_BASE_URL="http://$$GW/v1" NANOSERVE_EVAL_LIVE=1 \
	  uv run --group eval pytest evals -v $(EVAL_ARGS)

load:                   ## Locust smoke via the serving gateway (defaults 8u/1/s/2m)
	@GW=$$($(KUBECTL) get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}' 2>/dev/null); \
	test -n "$$GW" || { echo "error: kserve-gateway has no address (is it Programmed?)"; exit 1; }; \
	mkdir -p .locust; \
	echo "==> locust via http://$$GW (-u 8 -r 1 -t 2m; override with LOCUST_ARGS=...)"; \
	uv run --group load locust -f load_tests/locustfile.py \
	  --host "http://$$GW" \
	  --headless -u 8 -r 1 -t 2m --csv .locust/run $(LOCUST_ARGS)

stress:                 ## Locust realistic stress: ramp -> plateau -> spike hold
	@GW=$$($(KUBECTL) get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}' 2>/dev/null); \
	test -n "$$GW" || { echo "error: kserve-gateway has no address (is it Programmed?)"; exit 1; }; \
	mkdir -p .locust; \
	echo "==> stress via http://$$GW (shape: ramp 10s -> 18u 45s -> spike 48u hold 30s; 3K-token RAG ctx)"; \
	NANOSERVE_LOAD_SHAPE=1 NANOSERVE_LOAD_USERS=18 NANOSERVE_LOAD_SPIKE_USERS=48 \
	  NANOSERVE_LOAD_CONTEXT_TOKENS=3000 NANOSERVE_LOAD_WARMUP_S=10 \
	  NANOSERVE_LOAD_PLATEAU_S=45 NANOSERVE_LOAD_SPIKE_S=15 NANOSERVE_LOAD_SPIKE_HOLD_S=30 \
	  uv run --group load locust -f load_tests/locustfile.py \
	  --host "http://$$GW" \
	  --headless --csv .locust/stress $(STRESS_ARGS)

trace:                  ## Emit OTel spans to the collector (defaults to serving gateway)
	@GW=$$($(KUBECTL) get gateway kserve-gateway -n kserve -o jsonpath='{.status.addresses[0].value}' 2>/dev/null); \
	if [ -n "$$GW" ] && ! echo '$(TRACE_ARGS)' | grep -q -- '--url'; then \
	  export NANOSERVE_BASE_URL="http://$$GW/v1"; echo "==> trace via http://$$GW/v1"; \
	fi; \
	uv run --group dev python scripts/tracer.py $(TRACE_ARGS)

argo:                   ## Install Argo CD (full, non-HA) + bootstrap app-of-apps
	bash argo/install.sh

check:                  ## Run the full pre-commit gate
	uv run pre-commit run --all-files

clean:                  ## Remove local caches/artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .locust
