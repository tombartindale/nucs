#!/bin/sh
# One-time, per-environment AWS-side setup for the S3 backup bucket. Run manually with
# broader/admin AWS credentials (NOT the restricted nightly backup IAM user/role — that one
# should not have s3:PutBucketVersioning / s3:PutLifecycleConfiguration).
#
# MUST be run — enabling versioning — before backup.sh's `aws s3 sync --delete` runs against
# this bucket for the first time. Without versioning, --delete is a real, permanent delete;
# with it, a delete becomes a soft delete (a delete marker, prior content kept as a noncurrent
# version), which is what makes the nightly sync safe at all.
#
# Idempotent: safe to re-run, including after changing BACKUP_RETENTION_DAYS — the lifecycle
# rule's day count is baked in at setup time, not read live, so re-running is how a retention
# change actually takes effect.
#
# Usage: S3_BACKUP_BUCKET=... S3_BACKUP_PREFIX=postgres BACKUP_RETENTION_DAYS=14 ./setup-bucket.sh
set -eu

: "${S3_BACKUP_BUCKET:?S3_BACKUP_BUCKET is not set}"
prefix="${S3_BACKUP_PREFIX:-postgres}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"

echo "[setup-bucket] enabling versioning on s3://${S3_BACKUP_BUCKET}..."
aws s3api put-bucket-versioning \
  --bucket "$S3_BACKUP_BUCKET" \
  --versioning-configuration Status=Enabled

echo "[setup-bucket] applying lifecycle rules: expire noncurrent programme/ versions after ${retention_days} days, abort incomplete multipart uploads after 7 days..."
lifecycle="/tmp/beacon-backup-lifecycle.json"
cat > "$lifecycle" <<EOF
{
  "Rules": [
    {
      "ID": "expire-noncurrent-programme-versions",
      "Status": "Enabled",
      "Filter": { "Prefix": "${prefix}/programme/" },
      "NoncurrentVersionExpiration": { "NoncurrentDays": ${retention_days} }
    },
    {
      "ID": "abort-incomplete-multipart-uploads",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 }
    }
  ]
}
EOF
aws s3api put-bucket-lifecycle-configuration \
  --bucket "$S3_BACKUP_BUCKET" \
  --lifecycle-configuration "file://${lifecycle}"
rm -f "$lifecycle"

echo "[setup-bucket] done."
echo "[setup-bucket] note: db/ and manifest/ objects are uniquely named per run and never"
echo "[setup-bucket] overwritten, so they never produce a noncurrent version either way --"
echo "[setup-bucket] they are pruned directly by backup.sh's own retention loop instead."
