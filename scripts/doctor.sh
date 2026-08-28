#!/usr/bin/env bash
# Verify host prerequisites and pin integrity. Exit non-zero on any problem.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

fail=0
check() { if eval "$2"; then log "OK   $1"; else warn "FAIL $1"; fail=1; fi; }

# --- ordered semver compare: $1 >= $2 ? ---
ver_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -n1)" = "$2" ]
}

require_cmd docker
require_cmd kubectl
require_cmd kind
require_cmd helm
require_cmd python

log "docker daemon reachable?"
check "docker daemon" 'docker info >/dev/null 2>&1'

KIND_ACTUAL="$(kind version 2>/dev/null | awk "{print \$2}")"
check "kind version == $KIND_VERSION (got ${KIND_ACTUAL:-none})" \
      '[ "$KIND_ACTUAL" = "$KIND_VERSION" ]'

HELM_ACTUAL="$(helm version --template '{{.Version}}' 2>/dev/null | sed 's/^v//')"
check "helm >= $HELM_MIN_VERSION (got ${HELM_ACTUAL:-none})" \
      'ver_ge "$HELM_ACTUAL" "$HELM_MIN_VERSION"'

KUBECTL_ACTUAL="$(kubectl version --client -o json 2>/dev/null | python -c "import sys,json;print(json.load(sys.stdin)['clientVersion']['gitVersion'].lstrip('v'))" 2>/dev/null)"
check "kubectl >= $KUBECTL_MIN_VERSION (got ${KUBECTL_ACTUAL:-none})" \
      'ver_ge "$KUBECTL_ACTUAL" "$KUBECTL_MIN_VERSION"'

PY_ACTUAL="$(python -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)"
check "python >= $PYTHON_MIN_VERSION (got ${PY_ACTUAL:-none})" \
      'ver_ge "$PY_ACTUAL.0" "$PYTHON_MIN_VERSION.0"'

# --- digest integrity: kind-cluster.yaml must match versions.env -----------
CFG_IMAGE="$(grep -E "^\s*image:\s*kindest/node" "$REPO_ROOT/scripts/kind-cluster.yaml" | sed 's/^[[:space:]]*image:[[:space:]]*//')"
check "kind-cluster.yaml node image matches versions.env" \
      '[ "$CFG_IMAGE" = "$KIND_NODE_IMAGE" ]'
check "node image is digest-pinned (@sha256:)" \
      'printf "%s" "$KIND_NODE_IMAGE" | grep -q "@sha256:[0-9a-f]\{64\}$"'
check "no :latest anywhere in versions.env" \
      '! grep -q ":latest" "$REPO_ROOT/config/versions.env"'

if [ "$fail" -ne 0 ]; then die "doctor found problems (see FAIL lines above)"; fi
log "doctor: all checks passed"
