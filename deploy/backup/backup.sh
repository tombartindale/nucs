#!/bin/sh
# Nightly backup to S3, three things per run:
#   db/<stamp>.sql.gz    a Postgres dump: job history, preferences and module delivery plans.
#   programme/...        a live mirror of the programme tree (content, assets, master video,
#                        theme and pipeline state), kept in sync via `aws s3 sync` so only
#                        new/changed files are ever re-uploaded — unlike a full nightly zip,
#                        an unchanged multi-GB master video costs nothing on a quiet night.
#                        Scoped to exactly what `bcn transfer --full` would export (see
#                        tooling/bcn/tree.py NOISE, tooling/bcn/commands/transfer.py
#                        _export()), split into four disjoint syncs by data character.
#   manifest/<stamp>.json  a small {Key: VersionId} snapshot of programme/ as it stood after
#                        this run — not a content copy, just a few MB of JSON — so restoring
#                        the whole tree "as of" a past night is a scripted per-key version
#                        fetch (see restore-programme.sh --as-of) instead of an unscoped
#                        list-object-versions crawl.
#
# Point-in-time recovery for programme/ comes from S3 bucket versioning + a lifecycle rule
# (see setup-bucket.sh), NOT from dated snapshots, so programme/ itself is never pruned here.
# IMPORTANT: setup-bucket.sh must have been run against this bucket — enabling versioning —
# before this script's --delete syncs run for the first time, or a local deletion becomes an
# unrecoverable S3 deletion instead of a soft one.
#
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
stamp=$(date -u +%Y-%m-%dT%H-%M-%SZ)
dest="s3://${S3_BACKUP_BUCKET}/${prefix}/programme"

echo "[backup] dumping database..."
dump="/tmp/beacon-${stamp}.sql.gz"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h postgres -U beacon -d beacon | gzip > "$dump"
aws s3 cp "$dump" "s3://${S3_BACKUP_BUCKET}/${prefix}/db/${stamp}.sql.gz"
rm -f "$dump"

echo "[backup] mirroring programme tree (docs, assets, master video, theme, state)..."

# 1. Content docs + pipeline text state: topic text, module/unit docs, root config/state, and
#    the per-topic json/csv pipeline state (top-level only under build/out — transfer.py's
#    build.glob("*.json")/out.glob("*.json") don't recurse, so these excludes match that).
#    The broad "*.md" include also catches any stray untracked .md file anywhere in a topic,
#    which --full itself wouldn't export — accepted as harmless over-coverage for a backup.
#    The ".history/" exclude MUST come after "*.md": .history snapshots are named
#    "{stem}.{stamp}.md" (fsutil.save_with_history) and would otherwise match it, and the AWS
#    CLI applies --exclude/--include in order given, last match wins.
aws s3 sync "$programme" "$dest" \
  --delete \
  --exclude "*" \
  --include "*/topic.md" \
  --include "*/topic.zh.md" \
  --include "*/review.json" \
  --include "*/translation.json" \
  --include "*/build/*.json" \
  --include "*/build/*.csv" \
  --include "*/out/*.json" \
  --include "*.md" \
  --include "programme.toml" \
  --include "sync-state.json" \
  --exclude "*/build/*/*.json" \
  --exclude "*/build/*/*.csv" \
  --exclude "*/out/*/*.json" \
  --exclude "*/.history/*"

# 2. Assets (slide images etc.) — a whole-directory include, so noise patterns that could
#    land inside assets/ need excluding explicitly (mirrors tooling/bcn/tree.py's NOISE list;
#    keep these two lists in sync if NOISE ever changes).
aws s3 sync "$programme" "$dest" \
  --delete \
  --exclude "*" \
  --include "*/assets/*" \
  --exclude "*/assets/.DS_Store" \
  --exclude "*/assets/._*" \
  --exclude "*/assets/Icon*" \
  --exclude "*/assets/.localized" \
  --exclude "*/assets/.Spotlight-V100*" \
  --exclude "*/assets/.fseventsd*" \
  --exclude "*/assets/.TemporaryItems*" \
  --exclude "*/assets/.Trashes*" \
  --exclude "*/assets/__MACOSX*" \
  --exclude "*/assets/~\$*" \
  --exclude "*/assets/*~" \
  --exclude "*/assets/*.swp" \
  --exclude "*/assets/.~lock.*" \
  --exclude "*/assets/.*.partial*" \
  --exclude "*/assets/.*.tmp" \
  --exclude "*/assets/.out.old-*"

# 3. The master video + captions — the bulk of the data, and the whole reason this script
#    changed shape: unchanged masters are skipped entirely by sync, no re-upload, no local
#    re-staging (unlike the old zip, which re-read and re-wrote every master every night).
aws s3 sync "$programme" "$dest" \
  --delete \
  --exclude "*" \
  --include "*/edit/master.mp4" \
  --include "*/edit/master.srt" \
  --include "*/edit/master.zh.srt"

# 4. Custom theme assets (root-level, recursive).
aws s3 sync "$programme" "$dest" \
  --delete \
  --exclude "*" \
  --include "themes/custom/*" \
  --exclude "*/.DS_Store" \
  --exclude "*/._*" \
  --exclude "*/Icon*" \
  --exclude "*/.localized" \
  --exclude "*/.Spotlight-V100*" \
  --exclude "*/.fseventsd*" \
  --exclude "*/.TemporaryItems*" \
  --exclude "*/.Trashes*" \
  --exclude "*/__MACOSX*" \
  --exclude "*/~\$*" \
  --exclude "*/*~" \
  --exclude "*/*.swp" \
  --exclude "*/.~lock.*" \
  --exclude "*/.*.partial*" \
  --exclude "*/.*.tmp" \
  --exclude "*/.out.old-*"

echo "[backup] writing version manifest..."
manifest="/tmp/manifest-${stamp}.json"
aws s3api list-object-versions \
  --bucket "$S3_BACKUP_BUCKET" \
  --prefix "${prefix}/programme/" \
  --query 'Versions[?IsLatest==`true`].{Key:Key,VersionId:VersionId}' \
  --output json > "$manifest"
aws s3 cp "$manifest" "s3://${S3_BACKUP_BUCKET}/${prefix}/manifest/${stamp}.json"
rm -f "$manifest"

echo "[backup] pruning db/ and manifest/ backups older than ${retention_days} days..."
cutoff_epoch=$(( $(date -u +%s) - retention_days * 86400 ))
cutoff=$(date -u -d "@${cutoff_epoch}" +%Y-%m-%d)
for kind in db manifest; do
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
