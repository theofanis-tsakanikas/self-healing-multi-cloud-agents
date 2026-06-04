#!/usr/bin/env bash
#
# power.sh — pause/resume the GCP baseline to cut cost between work sessions
# WITHOUT destroying anything. Stops Cloud SQL (the 24/7 cost driver) and scales
# the GKE workloads to 0 replicas (Autopilot bills per running pod, so 0 pods ≈ 0
# compute). Everything else (GKE cluster, Artifact Registry, buckets, SA, all
# config + state) stays intact.
#
# Usage:
#   ./bootstrap/gcp/power.sh pause     # end of day  → stop Cloud SQL + scale pods to 0
#   ./bootstrap/gcp/power.sh resume    # next day    → start Cloud SQL + scale pods to 1
#   ./bootstrap/gcp/power.sh status    # show current power state
#
# Names are read from the bootstrap defaults; override via env if you changed them.
set -euo pipefail

PROJECT="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
DB="${GCP_SQL_INSTANCE:-multi-cloud-agent-mysql}"
CLUSTER="${GCP_GKE_CLUSTER:-multi-cloud-agent-gke}"
REGION="${GCP_REGION:-europe-west3}"

command -v gcloud >/dev/null 2>&1 || { echo "❌ gcloud not found. Install: brew install --cask google-cloud-sdk"; exit 1; }
[ -n "$PROJECT" ] || { echo "❌ No project. Set GCP_PROJECT_ID or run: gcloud config set project <id>"; exit 1; }

sql_state() { gcloud sql instances describe "$DB" --project="$PROJECT" --format="value(settings.activationPolicy)" 2>/dev/null || echo "NOT_FOUND"; }
_kube() { gcloud container clusters get-credentials "$CLUSTER" --region "$REGION" --project "$PROJECT" >/dev/null 2>&1; }

pause() {
  echo "⏸  Pausing GCP baseline (project: $PROJECT)…"
  case "$(sql_state)" in
    ALWAYS)    echo "  • Stopping Cloud SQL '$DB'…"; gcloud sql instances patch "$DB" --project="$PROJECT" --activation-policy=NEVER --quiet >/dev/null; echo "    ✅ Cloud SQL stopped." ;;
    NEVER)     echo "  • Cloud SQL '$DB' already stopped." ;;
    NOT_FOUND) echo "  • Cloud SQL '$DB' not found (not provisioned yet) — skipping." ;;
    *)         echo "  • Cloud SQL '$DB' in policy '$(sql_state)' — skipping." ;;
  esac
  if _kube; then
    echo "  • Scaling GKE workloads to 0 (Autopilot: no pods → no compute billing)…"
    kubectl scale deployment --all --replicas=0 -n analytics  >/dev/null 2>&1 || true
    kubectl scale deployment --all --replicas=0 -n monitoring >/dev/null 2>&1 || true
    echo "    ✅ Workloads scaled to 0."
  else
    echo "  • GKE '$CLUSTER' not reachable (not provisioned yet?) — skipping workload scale-down."
  fi
  echo "✅ Paused. Cloud SQL compute + GKE pod billing stopped (you still pay pennies for disks/AR/storage)."
  echo "ℹ️  Cloud SQL stays stopped until you 'resume' (no 7-day auto-restart on GCP)."
}

resume() {
  echo "▶️  Resuming GCP baseline (project: $PROJECT)…"
  case "$(sql_state)" in
    NEVER)     echo "  • Starting Cloud SQL '$DB'…"; gcloud sql instances patch "$DB" --project="$PROJECT" --activation-policy=ALWAYS --quiet >/dev/null; echo "    ✅ Cloud SQL ready." ;;
    ALWAYS)    echo "  • Cloud SQL '$DB' already running." ;;
    NOT_FOUND) echo "  • Cloud SQL '$DB' not found — skipping." ;;
    *)         echo "  • Cloud SQL '$DB' in policy '$(sql_state)' — skipping." ;;
  esac
  if _kube; then
    echo "  • Scaling GKE workloads back to 1…"
    kubectl scale deployment --all --replicas=1 -n analytics  >/dev/null 2>&1 || true
    kubectl scale deployment --all --replicas=1 -n monitoring >/dev/null 2>&1 || true
    echo "    ✅ Workloads scaled to 1."
  else
    echo "  • GKE '$CLUSTER' not reachable — skipping workload scale-up."
  fi
  echo "✅ Resumed."
}

status() {
  echo "📊 GCP baseline power state (project: $PROJECT)"
  echo "  • Cloud SQL '$DB': $(sql_state)   (ALWAYS = running, NEVER = stopped)"
  if _kube; then
    echo "  • GKE deployment replicas:"
    kubectl get deploy -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,REPLICAS:.spec.replicas --no-headers 2>/dev/null | grep -E "analytics|monitoring" || echo "    (no deployments)"
  else
    echo "  • GKE '$CLUSTER': not reachable"
  fi
  echo
  echo "ℹ️  'pause' does NOT remove a Grafana LoadBalancer. To drop that cost too,"
  echo "    delete the K8s LB services or use the cleanup workflow."
}

case "${1:-}" in
  pause)  pause ;;
  resume) resume ;;
  status) status ;;
  *) echo "Usage: $0 {pause|resume|status}"; exit 2 ;;
esac
