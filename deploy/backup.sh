#!/usr/bin/env bash
# Nightly backup: PostgreSQL dump + uploaded files, taken together, copied to S3.
#
#   ./backup.sh s3://<bucket>          (cron on the EC2 instance runs this; see Terraform)
#
# The bucket encrypts with KMS and expires old backups (lifecycle rule in Terraform).
# Restore steps are in docs/deployment.md.
set -euo pipefail

DEST="${1:?usage: backup.sh s3://bucket}"
cd "$(dirname "$0")"
ENV_FILE="${DEPLOY_ENV_FILE:-.env}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml)
STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
NEXUS_IMAGE_TAG="$(cat "${DEPLOY_STATE_FILE:-.deployed-tag}")"
export NEXUS_IMAGE_TAG

# Stream straight to S3 (no copy on local disk). If the dump or tar fails, pipefail
# fails the step and the partial object is deleted, so a truncated file never looks
# like a good backup.
upload() {
  local key="$1"
  shift
  if ! "$@" | aws s3 cp - "$key" --only-show-errors; then
    aws s3 rm "$key" --only-show-errors || true
    echo "backup failed: $key" >&2
    exit 1
  fi
}

upload "${DEST}/db/${STAMP}.dump" \
  "${COMPOSE[@]}" exec -T db pg_dump -U nexus -d nexusai --format=custom
upload "${DEST}/uploads/${STAMP}.tar.gz" \
  "${COMPOSE[@]}" exec -T backend tar -czf - -C /data/uploads .

echo "backup ${STAMP} done"
