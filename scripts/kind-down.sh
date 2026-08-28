#!/usr/bin/env bash
# Delete the kind cluster and clean project cluster state.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

require_cmd kind
assert_project_kubeconfig

if kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER_NAME"; then
  log "deleting kind cluster '$KIND_CLUSTER_NAME'"
  kind delete cluster --name "$KIND_CLUSTER_NAME" --kubeconfig "$KUBECONFIG"
else
  warn "cluster '$KIND_CLUSTER_NAME' not present — nothing to delete"
fi

rm -f "$CLUSTERS_DIR/$KIND_CLUSTER_NAME".* 2>/dev/null || true
rm -f "$KUBECONFIG" 2>/dev/null || true

log "cluster state cleaned"
