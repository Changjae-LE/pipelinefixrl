#!/usr/bin/env bash
# Create (idempotently) the pinned kind cluster with a project-only kubeconfig.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

require_cmd kind
require_cmd kubectl
require_cmd docker
assert_project_kubeconfig

docker info >/dev/null 2>&1 || die "docker daemon not reachable — start Docker Desktop"

CFG="$REPO_ROOT/scripts/kind-cluster.yaml"

if kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER_NAME"; then
  log "cluster '$KIND_CLUSTER_NAME' already exists — exporting kubeconfig"
  kind export kubeconfig --name "$KIND_CLUSTER_NAME" --kubeconfig "$KUBECONFIG"
else
  log "creating kind cluster '$KIND_CLUSTER_NAME' (node image pinned in $CFG)"
  kind create cluster \
    --name "$KIND_CLUSTER_NAME" \
    --config "$CFG" \
    --kubeconfig "$KUBECONFIG" \
    --wait 120s
fi

chmod 600 "$KUBECONFIG" 2>/dev/null || true
# On Windows/NTFS POSIX bits are cosmetic; restrict the real ACL to this user.
if command -v icacls >/dev/null 2>&1; then
  WIN_KC="$(cygpath -w "$KUBECONFIG" 2>/dev/null || echo "$KUBECONFIG")"
  icacls "$WIN_KC" /inheritance:r /grant:r "$USERNAME:F" >/dev/null 2>&1 || \
    warn "could not tighten ACL on $WIN_KC"
fi

log "waiting for node Ready"
kubectl wait --for=condition=Ready nodes --all --timeout=120s

NODE_COUNT="$(kubectl get nodes --no-headers | wc -l | tr -d ' ')"
[ "$NODE_COUNT" = "1" ] || die "expected exactly 1 node, found $NODE_COUNT"

kind get nodes --name "$KIND_CLUSTER_NAME" > "$CLUSTERS_DIR/$KIND_CLUSTER_NAME.nodes.txt"
kubectl get nodes -o json > "$CLUSTERS_DIR/$KIND_CLUSTER_NAME.json"
{
  echo "cluster=$KIND_CLUSTER_NAME"
  echo "kind_version=$(kind version)"
  echo "node_image=$KIND_NODE_IMAGE"
  echo "created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$CLUSTERS_DIR/$KIND_CLUSTER_NAME.meta"

log "cluster ready — kubeconfig at $KUBECONFIG"
kubectl get nodes -o wide
