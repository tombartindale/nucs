#!/bin/sh
# Nightly backup to S3, two objects per run:
#   full/<stamp>.zip  the whole programme (bcn transfer --full): content, media, theme and
#                     pipeline state. This is what restores a programme.
#   db/<stamp>.sql.gz a Postgres dump: job history, preferences and module delivery plans,
#                     which the programme export does not carry.
# Required env: POSTGRES_PASSWORD, S3_BACKUP_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY.
# Optional env: AWS_DEFAULT_REGION, S3_BACKUP_PREFIX (default: postgres), BACKUP_RETENTION_DAYS (default: 14).
set -eu

if [ -z "${S3_BACKUP_BUCKET:-}" ]; then
  echo "[backup] S3_BACKUP_BUCKET is not set, skipping (fine for local development)."
  exit 0
fi
prefix="${S3_BACKUP_PREFIX:-postgres}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
programme="${BEACON_ROOT:-/data/programme}"
bcn="/app/tooling/.venv/bin/bcn"
stamp=$(date -u +%Y-%m-%dT%H-%M-%SZ)

echo "[backup] dumping database..."
dump="/tmp/beacon-${stamp}.sql.gz"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h postgres -U beacon -d beacon | gzip > "$dump"
aws s3 cp "$dump" "s3://${S3_BACKUP_BUCKET}/${prefix}/db/${stamp}.sql.gz"
rm -f "$dump"

echo "[backup] exporting whole programme (bcn transfer --full)..."
envelope=$("$bcn" transfer "$programme" --export --full --quiet)
if printf '%s' "$envelope" | grep -q '"ok": true'; then
  zip_rel=$(printf '%s' "$envelope" | sed -n 's/.*"zip": "\([^"]*\)".*/\1/p')
  zip="${programme}/${zip_rel}"
  aws s3 cp "$zip" "s3://${S3_BACKUP_BUCKET}/${prefix}/full/${stamp}.zip"
  rm -f "$zip"
else
  echo "[backup] programme export produced no archive (nothing to export yet?):"
  printf '%s\n' "$envelope" | head -c 2000
fi

echo "[backup] pruning backups older than ${retention_days} days..."
cutoff_epoch=$(( $(date -u +%s) - retention_days * 86400 ))
cutoff=$(date -u -d "@${cutoff_epoch}" +%Y-%m-%d)
for kind in db full; do
  aws s3 ls "s3://${S3_BACKUP_BUCKET}/${prefix}/${kind}/" | while read -r _ _ _ key; do
    [ -z "$key" ] && continue
    file_date="${key%%T*}"
    if [ "$file_date" \< "$cutoff" ]; then
      echo "[backup] deleting old backup: ${kind}/${key}"
      aws s3 rm "s3://${S3_BACKUP_BUCKET}/${prefix}/${kind}/${key}"
    fi
  done
done

echo "[backup] done."
