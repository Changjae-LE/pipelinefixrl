#!/usr/bin/env bash
# Shared helpers. Source this from every script:  . "$(dirname "$0")/lib.sh"
set -euo pipefail

# --- repo root -------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- state layout (everything transient lives here) -----------------------
STATE_DIR="$REPO_ROOT/.state"
export KUBECONFIG="$STATE_DIR/kubeconfig"
CLUSTERS_DIR="$STATE_DIR/clusters"
RUNS_DIR="$STATE_DIR/runs"
mkdir -p "$STATE_DIR" "$CLUSTERS_DIR" "$RUNS_DIR"

# --- pinned versions -----------------------------------------------------
# shellcheck disable=SC1091
. "$REPO_ROOT/config/versions.env"

# --- make winget-installed tools discoverable in an already-open shell ---
_wg="$HOME/AppData/Local/Microsoft/WinGet"
for d in \
  "$_wg"/Links \
  "$_wg"/Packages/Kubernetes.kind_* \
  "$_wg"/Packages/Helm.Helm_*/windows-amd64 \
  "$_wg"/Packages/ezwinports.make_*/bin
do
  [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac
done
export PATH

# --- logging ------------------------------------------------------------
log()  { printf '\033[1;34m[pfrl]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[pfrl:warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[pfrl:err]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found on PATH: $1"
}

# kubeconfig guard: refuse to run against anything but the project kubeconfig.
assert_project_kubeconfig() {
  case "$KUBECONFIG" in
    "$STATE_DIR/kubeconfig") : ;;
    *) die "KUBECONFIG is not the project kubeconfig: $KUBECONFIG" ;;
  esac
}
