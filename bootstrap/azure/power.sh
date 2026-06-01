#!/usr/bin/env bash
#
# power.sh — pause/resume the Azure baseline to cut cost between work sessions
# WITHOUT destroying anything. Stops the AKS node pool (deallocates the VMs) and
# the PostgreSQL Flexible Server (stops compute billing). Everything else (ACR,
# storage, managed identity, all config + state) stays intact.
#
# Usage:
#   ./bootstrap/azure/power.sh pause     # end of day  → stop AKS + Postgres
#   ./bootstrap/azure/power.sh resume    # next day    → start AKS + Postgres
#   ./bootstrap/azure/power.sh status    # show current power state
#
# Names are read from the bootstrap defaults; override via env if you changed them.
set -euo pipefail

RG="${AZURE_RESOURCE_GROUP:-multi-cloud-agent-rg}"
AKS="${AZURE_AKS_CLUSTER:-multi-cloud-agent-aks}"
PG="${AZURE_PG_SERVER:-multi-cloud-agent-pg}"

command -v az >/dev/null 2>&1 || { echo "❌ az CLI not found. Install with: brew install azure-cli"; exit 1; }
az account show >/dev/null 2>&1 || { echo "❌ Not logged in. Run: az login"; exit 1; }

aks_state() { az aks show -g "$RG" -n "$AKS" --query "powerState.code" -o tsv 2>/dev/null || echo "NOT_FOUND"; }
pg_state()  { az postgres flexible-server show -g "$RG" -n "$PG" --query "state" -o tsv 2>/dev/null || echo "NOT_FOUND"; }

pause() {
  echo "⏸  Pausing Azure baseline (RG: $RG)…"
  case "$(aks_state)" in
    Running)   echo "  • Stopping AKS '$AKS' (deallocates nodes)…"; az aks stop  -g "$RG" -n "$AKS" -o none; echo "    ✅ AKS stopped." ;;
    Stopped)   echo "  • AKS '$AKS' already stopped." ;;
    NOT_FOUND) echo "  • AKS '$AKS' not found (not provisioned yet) — skipping." ;;
    *)         echo "  • AKS '$AKS' in transitional state — skipping." ;;
  esac
  case "$(pg_state)" in
    Ready)     echo "  • Stopping PostgreSQL '$PG'…"; az postgres flexible-server stop -g "$RG" -n "$PG" -o none; echo "    ✅ Postgres stopped." ;;
    Stopped|Disabled) echo "  • PostgreSQL '$PG' already stopped." ;;
    NOT_FOUND) echo "  • PostgreSQL '$PG' not found — skipping." ;;
    *)         echo "  • PostgreSQL '$PG' in state '$(pg_state)' — skipping." ;;
  esac
  echo "✅ Paused. Compute billing stopped (you still pay pennies for disks/ACR/storage)."
  echo "ℹ️  Note: Azure auto-starts a stopped Postgres after ~7 days. Fine for overnight."
}

resume() {
  echo "▶️  Resuming Azure baseline (RG: $RG)…"
  case "$(aks_state)" in
    Stopped)   echo "  • Starting AKS '$AKS'…"; az aks start -g "$RG" -n "$AKS" -o none; echo "    ✅ AKS running." ;;
    Running)   echo "  • AKS '$AKS' already running." ;;
    NOT_FOUND) echo "  • AKS '$AKS' not found — skipping." ;;
    *)         echo "  • AKS '$AKS' in transitional state — skipping." ;;
  esac
  case "$(pg_state)" in
    Stopped|Disabled) echo "  • Starting PostgreSQL '$PG'…"; az postgres flexible-server start -g "$RG" -n "$PG" -o none; echo "    ✅ Postgres ready." ;;
    Ready)     echo "  • PostgreSQL '$PG' already running." ;;
    NOT_FOUND) echo "  • PostgreSQL '$PG' not found — skipping." ;;
    *)         echo "  • PostgreSQL '$PG' in state '$(pg_state)' — skipping." ;;
  esac
  echo "✅ Resumed."
}

status() {
  echo "📊 Azure baseline power state (RG: $RG)"
  echo "  • AKS '$AKS':       $(aks_state)"
  echo "  • PostgreSQL '$PG': $(pg_state)"
  echo
  echo "ℹ️  'pause' does NOT remove a Grafana LoadBalancer (created in Run #2). To drop"
  echo "    that ~\$0.6/day too, delete the K8s LB services or use the cleanup workflow."
}

case "${1:-}" in
  pause)  pause ;;
  resume) resume ;;
  status) status ;;
  *) echo "Usage: $0 {pause|resume|status}"; exit 2 ;;
esac
