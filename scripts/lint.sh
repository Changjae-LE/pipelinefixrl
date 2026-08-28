#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

require_cmd helm
log "helm lint charts/app"
helm lint "$REPO_ROOT/charts/app"

log "helm template smoke (render with a fake tag)"
helm template app "$REPO_ROOT/charts/app" --set image.tag=lint-only >/dev/null
log "lint OK"
