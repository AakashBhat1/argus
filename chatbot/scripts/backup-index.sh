#!/usr/bin/env bash
# scripts/backup-index.sh
# Backup FAISS index files from the chatbot_data Docker volume to S3.
# Restore flow is documented at the bottom of this script.
#
# Prerequisites:
#   - AWS CLI configured (EC2 instance role or aws configure)
#   - S3_BUCKET environment variable set, e.g.: export S3_BUCKET=my-chatbot-data
#
# Usage:
#   S3_BUCKET=my-chatbot-data ./scripts/backup-index.sh          # backup
#   S3_BUCKET=my-chatbot-data ./scripts/backup-index.sh restore  # restore

set -euo pipefail

BUCKET="${S3_BUCKET:?Set S3_BUCKET=<your-bucket-name>}"
S3_PREFIX="chatbot/index"
# Temp directory on the host to stage files from the Docker volume
STAGE_DIR="/tmp/chatbot-index-backup"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-chatbot}"

ACTION="${1:-backup}"

case "$ACTION" in

backup)
  echo "==> Backing up FAISS index to s3://${BUCKET}/${S3_PREFIX}/"

  mkdir -p "$STAGE_DIR"

  # Copy files out of the named volume via a throwaway container
  docker run --rm \
    -v "${COMPOSE_PROJECT}_chatbot_data:/data:ro" \
    -v "${STAGE_DIR}:/backup" \
    busybox \
    sh -c "cp -r /data/. /backup/"

  TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
  ARCHIVE="${STAGE_DIR}/index-${TIMESTAMP}.tar.gz"
  tar -czf "$ARCHIVE" -C "$STAGE_DIR" \
    faiss.index documents.pkl embeddings.npy checkpoint.txt checkpoint.json \
    2>/dev/null || true  # ignore missing optional files

  aws s3 cp "$ARCHIVE" "s3://${BUCKET}/${S3_PREFIX}/index-${TIMESTAMP}.tar.gz"
  aws s3 cp "$ARCHIVE" "s3://${BUCKET}/${S3_PREFIX}/index-latest.tar.gz"

  rm -rf "$STAGE_DIR"
  echo "    Backup complete: s3://${BUCKET}/${S3_PREFIX}/index-${TIMESTAMP}.tar.gz"
  ;;

restore)
  echo "==> Restoring FAISS index from s3://${BUCKET}/${S3_PREFIX}/index-latest.tar.gz"

  mkdir -p "$STAGE_DIR"

  aws s3 cp "s3://${BUCKET}/${S3_PREFIX}/index-latest.tar.gz" "${STAGE_DIR}/index-latest.tar.gz"
  tar -xzf "${STAGE_DIR}/index-latest.tar.gz" -C "$STAGE_DIR"

  # Copy files into the named volume via a throwaway container
  docker run --rm \
    -v "${COMPOSE_PROJECT}_chatbot_data:/data" \
    -v "${STAGE_DIR}:/backup:ro" \
    busybox \
    sh -c "cp -r /backup/. /data/"

  rm -rf "$STAGE_DIR"
  echo "    Restore complete. Restart the chatbot to reload the index."
  echo "    docker compose restart chatbot"
  ;;

*)
  echo "Usage: $0 [backup|restore]"
  exit 1
  ;;

esac
