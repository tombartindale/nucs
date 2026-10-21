#!/bin/sh
# One-time, per-environment AWS-side setup for the S3 *media* bucket (master video and the
# composed/delivered mp4s). Run by hand with admin AWS credentials, not the restricted ones
# the api and workers use.
#
# The bucket is the only copy of the video, so:
#   - versioning is enabled: replacing or deleting a video keeps the old one as a noncurrent
#     version, recoverable until it expires;
#   - public access is blocked (browsers only ever get short-lived presigned URLs);
#   - browser uploads go straight to the bucket, so it needs a CORS rule for the app's origin,
#     exposing the ETag header (the browser must read each part's ETag to finish the upload);
#   - abandoned browser uploads (<prefix>/_incoming/) and incomplete multipart uploads expire;
#   - remote runner hand-off archives (<prefix>/_jobs/, see deploy/RUNNER.md) expire; the
#     server deletes each job's as it finishes, so these are only ones a crash left behind.
#
# Idempotent: safe to re-run, including to change retention or the allowed origin.
#
# Usage: S3_MEDIA_BUCKET=... APP_ORIGIN=https://beacon.example.edu \
#        [S3_MEDIA_PREFIX=media] [MEDIA_NONCURRENT_DAYS=90] ./setup-bucket.sh
set -eu

: "${S3_MEDIA_BUCKET:?S3_MEDIA_BUCKET is not set}"
: "${APP_ORIGIN:?APP_ORIGIN is not set (the https:// origin the app is served from, no trailing slash)}"
prefix="${S3_MEDIA_PREFIX:-media}"
noncurrent_days="${MEDIA_NONCURRENT_DAYS:-90}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "[media-bucket] blocking public access on s3://${S3_MEDIA_BUCKET}..."
aws s3api put-public-access-block --bucket "$S3_MEDIA_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "[media-bucket] enabling versioning..."
aws s3api put-bucket-versioning --bucket "$S3_MEDIA_BUCKET" --versioning-configuration Status=Enabled

echo "[media-bucket] applying CORS for ${APP_ORIGIN}..."
cat > "$tmp/cors.json" <<JSON
{
  "CORSRules": [
    {
      "AllowedOrigins": ["${APP_ORIGIN}"],
      "AllowedMethods": ["GET", "HEAD", "PUT"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["ETag", "Content-Length", "Content-Range", "Accept-Ranges"],
      "MaxAgeSeconds": 3600
    }
  ]
}
JSON
aws s3api put-bucket-cors --bucket "$S3_MEDIA_BUCKET" --cors-configuration "file://${tmp}/cors.json"

echo "[media-bucket] applying lifecycle rules..."
cat > "$tmp/lifecycle.json" <<JSON
{
  "Rules": [
    {
      "ID": "expire-noncurrent-media-versions",
      "Status": "Enabled",
      "Filter": { "Prefix": "${prefix}/" },
      "NoncurrentVersionExpiration": { "NoncurrentDays": ${noncurrent_days} }
    },
    {
      "ID": "expire-abandoned-browser-uploads",
      "Status": "Enabled",
      "Filter": { "Prefix": "${prefix}/_incoming/" },
      "Expiration": { "Days": 2 },
      "NoncurrentVersionExpiration": { "NoncurrentDays": 1 }
    },
    {
      "ID": "expire-runner-job-archives",
      "Status": "Enabled",
      "Filter": { "Prefix": "${prefix}/_jobs/" },
      "Expiration": { "Days": 7 },
      "NoncurrentVersionExpiration": { "NoncurrentDays": 1 }
    },
    {
      "ID": "abort-incomplete-multipart-uploads",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 2 }
    }
  ]
}
JSON
aws s3api put-bucket-lifecycle-configuration --bucket "$S3_MEDIA_BUCKET" --lifecycle-configuration "file://${tmp}/lifecycle.json"

echo "[media-bucket] done. Move existing video over with: bcn media <programme root> migrate --apply --delete"
