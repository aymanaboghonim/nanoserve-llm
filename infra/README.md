# Phase 2 — Infrastructure Bootstrap (WSL2 & K3s)

Brings the 12GB-RAM / 8GB-VRAM box from bare WSL2 to a GPU-enabled k3s cluster
with the Gateway API platform stack that KServe's `LLMInferenceService` needs
in Phase 3.

## Architecture

```
Windows host                     WSL2 VM (12GB RAM fence, 16GB swap)
┌──────────────────────┐        ┌─────────────────────────────────────────┐
│ .wslconfig           │        │ systemd ── k3s server (v1.36.3+k3s1)    │
│  memory=12GB         │ ─────► │   ├─ traefik: DISABLED (reclaims RAM)    │
│  swap=16GB           │        │   ├─ servicelb (klipper-lb): Gateway LB  │
│  mirrored networking │        │   ├─ kubelet fence: 1Gi reserved/evict   │
│  autoMemoryReclaim   │        │   └─ containerd ── nvidia runtime (CDI)  │
│  sparseVhd           │        │         └── RTX 3070 (WSL paravirt)     │
│                      │        │   nvidia-device-plugin → nvidia.com/gpu  │
└──────────────────────┘        └─────────────────────────────────────────┘
```

## Runbook (top to bottom)

### 0. Prereqs
- Windows 11 22H2+ (for `[experimental]` WSL keys), WSL2, systemd enabled.
- `nvidia-smi` works inside WSL (driver present — paravirtualized, no Linux driver needed).

### 1. Host fencing — `infra/wslconfig.example`
Merge into `%UserProfile%\.wslconfig` (already exists here — merge, don't clobber),
then **from PowerShell**: `wsl --shutdown` and reopen the distro.

> ⚠️ `wsl --shutdown` kills **all** running WSL sessions including this one.

Verify:
```bash
free -h          # Mem ≈ 12G
swapon --show    # 16G
```

### 2. CLIs + k3s (root only for bootstrap)
```bash
bash infra/install-clis.sh      # kubectl/helm/yq → ~/.local/bin (no root)
sudo bash infra/bootstrap.sh    # nvidia-container-toolkit + k3s + config + enable
```

k3s flags (from `infra/k3s/config.yaml` → `/etc/rancher/k3s/config.yaml`):
- `disable: [traefik]` — reclaim ~100-200MB (blueprint)
- `kubelet-arg: system-reserved=memory=1Gi, eviction-hard=memory.available<1Gi` — kubelet-level OOM fencing
- `node-label: gpu=true`, `write-kubeconfig-mode: 0644`

### 3. GPU bridge
k3s auto-detects `nvidia-container-runtime` and creates the `nvidia` RuntimeClass:
```bash
grep nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml
kubectl get runtimeclasses
kubectl apply -k manifests/infra      # device plugin + smoke pod
kubectl logs -n gpu-test -l app=gpu-test --tail=10   # expect "RTX 3070"
```
> ⚠️ NVIDIA known issue: avoid `systemctl daemon-reload` after k3s is running
> (can break container GPU access). Restart k3s instead if needed.

### 4. Gateway API stack (extended scope)
```bash
bash manifests/infra/gateway-stack.sh
curl -s http://localhost:PORT/hello   # from Windows or WSL; see printed address
```

### 5. Verification gate
```bash
bash infra/verify.sh        # exit 0 = all green
```

## Memory budget (12GB fence)

| Layer | ~RSS |
|---|---|
| k3s (server+addons) | ~1.0 GB |
| cert-manager | ~0.15 GB |
| Envoy Gateway + AI Gateway | ~0.45 GB |
| device plugin | ~0.05 GB |
| **Phase 2 total** | **~1.7 GB** |
| + Phase 3 (KServe ctrl + LWS + vLLM pod) | ~4.0 GB |
| + Phase 4 (Prometheus + Grafana) | ~4.6 GB |
| + host tooling | ~5.6 GB of 12 GB — swap is the burst buffer |

## Pinned versions

| Component | Version | Source |
|---|---|---|
| k3s | v1.36.3+k3s1 | get.k3s.io |
| nvidia-container-toolkit | 1.20.0-1 | nvidia.github.io/libnvidia-container |
| cert-manager | v1.18.x | charts.jetstack.io |
| Gateway API CRDs | v1.5.1 | vendored `gateway-api/` |
| GIE CRDs | v1.5.0 | vendored `gateway-api/` |
| Envoy Gateway | latest from helm (KServe needs ≥ v1.5.0) | helm.envoyproxy.io |
| Envoy AI Gateway | latest from helm (KServe needs ≥ v1.0.0) | envoyproxy.github.io/ai-gateway |
| NVIDIA device plugin | pinned in `device-plugin.yaml` | NVIDIA releases |

## Troubleshooting
- **Gateway/hello route unreachable after a WSL restart** → WSL2 may reassign the
  node IP. Resolve the current address dynamically:
  `kubectl get gateway -n kserve kserve-gateway -o jsonpath='{.status.addresses[0].value}'`.
  `infra/verify.sh` already does this.
- **k3s crash-looping with clean exits (`NRestarts` climbing)** → cgroup v1. The
  k3s v1.36 kubelet requires cgroup v2: `journalctl -u k3s | grep cgroup` shows
  "kubelet is configured to not run on a host using cgroup v1". Fix: add
  `kernelCommandLine = cgroup_no_v1=all systemd.unified_cgroup_hierarchy=1` to
  `.wslconfig` and `wsl --shutdown`. Verify: `stat -fc %T /sys/fs/cgroup` →
  `cgroup2fs`.
- **k3s not auto-starting on WSL boot** → `systemctl is-enabled k3s`; systemd must be on (`/etc/wsl.conf` → `[boot] systemd=true`).
- **GPU pod stuck Pending** → `kubectl describe pod -n gpu-test gpu-smoke`; check RuntimeClass + device-plugin pods.
- **Gateway not programmed** → `kubectl describe gateway -n kserve`; ensure servicelb not disabled.
- **OOM during model load (Phase 3)** → confirm fence: `free -h`, `kubectl describe node | grep -i evict`.
