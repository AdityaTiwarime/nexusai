#!/usr/bin/env bash
# Deploy one image tag of NexusAI on this server, and roll back if it isn't healthy.
#
#   ./deploy.sh <image-tag>        e.g. ./deploy.sh 3f2c1ab   (the git commit SHA)
#
# 1. Pull the tag's images and restart the stack with them.
# 2. Check the site through Caddy, the way users reach it (page + API readiness).
# 3. Healthy: remember the tag. Not healthy: start the previous tag again.
#
# Exit codes: 0 deployed, 1 failed (rolled back, or nothing to roll back to),
#             2 failed and the rollback is unhealthy too (needs a person).
#
# Database migrations run when the backend starts and are not undone by a rollback,
# so every migration must work with the previous release too (add columns first,
# remove them a release later). See docs/deployment.md.
set -euo pipefail

TAG="${1:?usage: deploy.sh <image-tag>}"
cd "$(dirname "$0")"

ENV_FILE="${DEPLOY_ENV_FILE:-.env}"
STATE_FILE="${DEPLOY_STATE_FILE:-.deployed-tag}"
TIMEOUT="${HEALTH_TIMEOUT_SECONDS:-180}"
INTERVAL="${HEALTH_INTERVAL_SECONDS:-3}"
# Extra curl options, e.g. "-k" for Caddy's local certificate when NEXUS_DOMAIN=localhost.
read -r -a CURL_OPTS <<< "${DEPLOY_CURL_OPTS:-}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml)
[[ -n "${DEPLOY_PROFILES:-}" ]] && COMPOSE+=(--profile "$DEPLOY_PROFILES")

set -a
# shellcheck disable=SC1090
. "./$ENV_FILE"
set +a
: "${NEXUS_DOMAIN:?NEXUS_DOMAIN must be set in $ENV_FILE}"

log() { printf '%s deploy: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# `set -e` doesn't apply inside a function called from `&&`, so failures return explicitly.
start() {
  export NEXUS_IMAGE_TAG="$1"
  if [[ "${DEPLOY_SKIP_PULL:-0}" != "1" ]]; then
    "${COMPOSE[@]}" pull --quiet || return 1
  fi
  "${COMPOSE[@]}" up -d --no-build --remove-orphans || return 1
}

healthy() {
  # Resolve the domain to this machine so the check goes through Caddy and its certificate.
  local base="https://${NEXUS_DOMAIN}" deadline=$((SECONDS + TIMEOUT))
  local resolve=(--resolve "${NEXUS_DOMAIN}:443:127.0.0.1")
  while ((SECONDS < deadline)); do
    if curl -fsS --max-time 5 "${resolve[@]}" "${CURL_OPTS[@]}" "$base/api/v1/health/ready" >/dev/null 2>&1 &&
      curl -fsS --max-time 5 "${resolve[@]}" "${CURL_OPTS[@]}" "$base/" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$INTERVAL"
  done
  return 1
}

previous="$(cat "$STATE_FILE" 2>/dev/null || true)"
log "deploying ${TAG} (currently running: ${previous:-nothing})"

if start "$TAG" && healthy; then
  echo "$TAG" >"$STATE_FILE"
  log "deployed ${TAG}: healthy"
  exit 0
fi

log "${TAG} is not healthy after ${TIMEOUT}s"
"${COMPOSE[@]}" ps || true
"${COMPOSE[@]}" logs --tail 50 backend worker || true

if [[ -z "$previous" || "$previous" == "$TAG" ]]; then
  log "no earlier release to roll back to"
  exit 1
fi

log "rolling back to ${previous}"
if start "$previous" && healthy; then
  log "rolled back to ${previous}: healthy"
  exit 1
fi
log "rollback to ${previous} is unhealthy too: manual action needed"
exit 2
