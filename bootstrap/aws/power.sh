#!/usr/bin/env bash
#
# power.sh — pause/resume the AWS baseline to cut cost between work sessions
# WITHOUT destroying anything. Stops the RDS instance (the 24/7 cost driver) and
# scales the EKS managed node groups to 0 (deallocates the EC2 worker nodes).
# Everything else (EKS control plane, ECR, S3, IAM, all config + state) stays.
#
# The EKS bootstrap has no Terraform-managed node group, so node groups are
# discovered dynamically; if none exist, only RDS is paused (graceful).
#
# Usage:
#   ./bootstrap/aws/power.sh pause     # end of day  → stop RDS + scale nodes to 0
#   ./bootstrap/aws/power.sh resume    # next day    → start RDS + scale nodes up
#   ./bootstrap/aws/power.sh status    # show current power state
#
# Names/region read from bootstrap defaults; override via env if you changed them.
set -euo pipefail

CLUSTER="${AWS_EKS_CLUSTER:-multi-cloud-agent-cluster}"
RDS="${AWS_RDS_INSTANCE:-eu-sales-raw-data}"
REGION="${AWS_DEFAULT_REGION:-eu-central-1}"
RESUME_DESIRED="${AWS_RESUME_DESIRED:-2}"

command -v aws >/dev/null 2>&1 || { echo "❌ aws CLI not found."; exit 1; }
aws sts get-caller-identity >/dev/null 2>&1 || { echo "❌ Not authenticated. Configure AWS credentials."; exit 1; }

rds_state()  { aws rds describe-db-instances --db-instance-identifier "$RDS" --region "$REGION" --query "DBInstances[0].DBInstanceStatus" --output text 2>/dev/null || echo "NOT_FOUND"; }
nodegroups() { aws eks list-nodegroups --cluster-name "$CLUSTER" --region "$REGION" --query "nodegroups[]" --output text 2>/dev/null || true; }
ng_max()     { aws eks describe-nodegroup --cluster-name "$CLUSTER" --nodegroup-name "$1" --region "$REGION" --query "nodegroup.scalingConfig.maxSize" --output text 2>/dev/null || echo ""; }
ng_desired() { aws eks describe-nodegroup --cluster-name "$CLUSTER" --nodegroup-name "$1" --region "$REGION" --query "nodegroup.scalingConfig.desiredSize" --output text 2>/dev/null || echo "?"; }

pause() {
  echo "⏸  Pausing AWS baseline (region: $REGION)…"
  case "$(rds_state)" in
    available)        echo "  • Stopping RDS '$RDS'…"; aws rds stop-db-instance --db-instance-identifier "$RDS" --region "$REGION" >/dev/null; echo "    ✅ RDS stopping." ;;
    stopped|stopping) echo "  • RDS '$RDS' already stopped/stopping." ;;
    NOT_FOUND)        echo "  • RDS '$RDS' not found (not provisioned yet) — skipping." ;;
    *)                echo "  • RDS '$RDS' in state '$(rds_state)' — only 'available' can be stopped; skipping." ;;
  esac
  local ngs; ngs="$(nodegroups)"
  if [ -n "$ngs" ]; then
    for ng in $ngs; do
      local maxv; maxv="$(ng_max "$ng")"; [ -n "$maxv" ] || maxv=1
      echo "  • Scaling node group '$ng' to 0…"
      aws eks update-nodegroup-config --cluster-name "$CLUSTER" --nodegroup-name "$ng" --region "$REGION" \
        --scaling-config minSize=0,maxSize="$maxv",desiredSize=0 >/dev/null && echo "    ✅ '$ng' scaling to 0."
    done
  else
    echo "  • No managed node groups on '$CLUSTER' — skipping (RDS still paused)."
  fi
  echo "✅ Paused. RDS + EKS worker nodes stopped."
  echo "ℹ️  EKS control plane (~\$0.10/hr) cannot be paused — only 'destroy' removes it."
  echo "ℹ️  AWS auto-starts a stopped RDS after 7 days. Fine for overnight."
}

resume() {
  echo "▶️  Resuming AWS baseline (region: $REGION)…"
  case "$(rds_state)" in
    stopped)           echo "  • Starting RDS '$RDS'…"; aws rds start-db-instance --db-instance-identifier "$RDS" --region "$REGION" >/dev/null; echo "    ✅ RDS starting." ;;
    available|starting) echo "  • RDS '$RDS' already running/starting." ;;
    NOT_FOUND)         echo "  • RDS '$RDS' not found — skipping." ;;
    *)                 echo "  • RDS '$RDS' in state '$(rds_state)' — skipping." ;;
  esac
  local ngs; ngs="$(nodegroups)"
  for ng in $ngs; do
    local maxv; maxv="$(ng_max "$ng")"; [ -n "$maxv" ] || maxv="$RESUME_DESIRED"
    if [ "$maxv" -lt "$RESUME_DESIRED" ] 2>/dev/null; then maxv="$RESUME_DESIRED"; fi
    echo "  • Scaling node group '$ng' to $RESUME_DESIRED…"
    aws eks update-nodegroup-config --cluster-name "$CLUSTER" --nodegroup-name "$ng" --region "$REGION" \
      --scaling-config minSize=1,maxSize="$maxv",desiredSize="$RESUME_DESIRED" >/dev/null && echo "    ✅ '$ng' scaling to $RESUME_DESIRED."
  done
  echo "✅ Resumed."
}

status() {
  echo "📊 AWS baseline power state (region: $REGION)"
  echo "  • RDS '$RDS': $(rds_state)"
  local ngs; ngs="$(nodegroups)"
  if [ -n "$ngs" ]; then
    for ng in $ngs; do echo "  • node group '$ng': desired=$(ng_desired "$ng")"; done
  else
    echo "  • node groups: none found on '$CLUSTER'"
  fi
}

case "${1:-}" in
  pause)  pause ;;
  resume) resume ;;
  status) status ;;
  *) echo "Usage: $0 {pause|resume|status}"; exit 2 ;;
esac
