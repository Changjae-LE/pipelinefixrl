#!/usr/bin/env bash
# CI pre-deploy gate (base-v2 / M-BE). Exits non-zero on the first failure.
# Consumed by scenario-009 as the `ci_gate_pass` check; runnable via `make ci`.
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

require_cmd helm
require_cmd docker

PY="${PYTHON:-$REPO_ROOT/.venv/Scripts/python.exe}"
[ -x "$PY" ] || PY="$(command -v python)"

log "ci: pytest"
"$PY" -m pytest -q

# Non-production placeholder tag so `helm lint` can fully render the chart.
# This does NOT weaken the chart: the `required` guard on image.tag still fires
# for any real deploy that omits the tag (see charts/app/templates/deployment.yaml).
CI_PLACEHOLDER_TAG="ci-lint-only"

log "ci: helm lint"
helm lint "$REPO_ROOT/charts/app" --set image.tag="$CI_PLACEHOLDER_TAG"

log "ci: helm template smoke"
helm template ci "$REPO_ROOT/charts/app" --set image.tag="$CI_PLACEHOLDER_TAG" >/dev/null

log "ci: docker build"
docker build -f "$REPO_ROOT/docker/Dockerfile" -t pipelinefixrl/app:ci-smoke "$REPO_ROOT" >/dev/null

log "ci: pin policy"
if grep -rnE ':latest' "$REPO_ROOT/charts" "$REPO_ROOT/docker" "$REPO_ROOT/config" 2>/dev/null; then
  die "found a ':latest' image reference"
fi
if grep -nqE '^FROM[[:space:]]+python[[:space:]]*(AS|$)' "$REPO_ROOT/docker/Dockerfile"; then
  die "unpinned base image in docker/Dockerfile"
fi

log "ci: OK"
