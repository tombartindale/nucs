#!/bin/sh
# Restores the programme tree (content, assets, master video, theme, pipeline state) from the
# S3 mirror backup.sh keeps. Postgres restore is handled separately by restore.sh -- not here.
#
# Usage:
#   ./restore-programme.sh [--delete] [REL_PATH]
#       Pulls the CURRENT ("latest") version of every object under programme/ (optionally
#       narrowed to REL_PATH, e.g. "KV7016/U01/T01") down onto $BEACON_ROOT. This is the
#       disaster-recovery case: a dead host, a fresh volume, "get me back to where things are
#       right now". Without --delete, nothing already on disk is removed (a locally-generated
#       build/ tree, never backed up, is left alone); --delete makes it an exact mirror.
#
#   ./restore-programme.sh --as-of STAMP [REL_PATH]
#       Rolls the tree (or just REL_PATH) back to how it looked as of a specific past backup
#       run, using that run's manifest/STAMP.json ({Key, VersionId} for every object at that
#       time) rather than whatever is current now. Needs jq. STAMP is a manifest name as
#       listed by `aws s3 ls s3://$S3_BACKUP_BUCKET/$S3_BACKUP_PREFIX/manifest/`, e.g.
#       2026-10-06T03-17-00Z (the ".json" suffix is optional).
#
# Recovering ONE specific file as of a specific point in time (e.g. "this master.mp4 was
# corrupted locally and the corruption already synced up") doesn't need a manifest -- the
# key's own version history already has it:
#   aws s3api list-object-versions --bucket "$S3_BACKUP_BUCKET" \
#     --prefix "$S3_BACKUP_PREFIX/programme/KV7016/U01/T01/edit/master.mp4"
#   # pick the VersionId you want from the output, then:
#   aws s3api get-object --bucket "$S3_BACKUP_BUCKET" \
#     --key "$S3_BACKUP_PREFIX/programme/KV7016/U01/T01/edit/master.mp4" \
#     --version-id "<VersionId>" \
#     "$BEACON_ROOT/KV7016/U01/T01/edit/master.mp4"
#
# Required env: S3_BACKUP_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY.
# Optional env: AWS_DEFAULT_REGION, S3_BACKUP_PREFIX (default: postgres), BEACON_ROOT (default: /data/programme).
set -eu

: "${S3_BACKUP_BUCKET:?S3_BACKUP_BUCKET is not set}"
prefix="${S3_BACKUP_PREFIX:-postgres}"
root="${BEACON_ROOT:-/data/programme}"

as_of=""
do_delete=false
rel=""

while [ $# -gt 0 ]; do
  case "$1" in
    --as-of)
      as_of="${2:?--as-of needs a STAMP argument}"
      shift 2
      ;;
    --delete)
      do_delete=true
      shift
      ;;
    *)
      rel="$1"
      shift
      ;;
  esac
done

if [ -n "$as_of" ]; then
  stamp="${as_of%.json}"
  manifest_key="${prefix}/manifest/${stamp}.json"
  tmp_manifest="/tmp/restore-manifest-${stamp}.json"
  echo "[restore] downloading manifest s3://${S3_BACKUP_BUCKET}/${manifest_key}"
  aws s3 cp "s3://${S3_BACKUP_BUCKET}/${manifest_key}" "$tmp_manifest"

  echo "[restore] restoring programme tree as of ${stamp}${rel:+ (scoped to $rel)}..."
  jq -r '.[] | "\(.Key)\t\(.VersionId)"' "$tmp_manifest" | while IFS="$(printf '\t')" read -r key vid; do
    sub="${key#"${prefix}"/programme/}"
    if [ -n "$rel" ]; then
      case "$sub" in
        "$rel"|"$rel"/*) ;;
        *) continue ;;
      esac
    fi
    dest="${root}/${sub}"
    mkdir -p "$(dirname "$dest")"
    echo "[restore] ${sub} @ ${vid}"
    aws s3api get-object --bucket "$S3_BACKUP_BUCKET" --key "$key" --version-id "$vid" "$dest" > /dev/null
  done
  rm -f "$tmp_manifest"
  echo "[restore] done."
  exit 0
fi

src="s3://${S3_BACKUP_BUCKET}/${prefix}/programme${rel:+/${rel}}"
dst="${root}${rel:+/${rel}}"
echo "[restore] restoring latest programme tree from ${src} to ${dst}..."
if [ "$do_delete" = true ]; then
  echo "[restore] --delete passed: this will remove local files not present in the backup."
  aws s3 sync "$src" "$dst" --delete
else
  aws s3 sync "$src" "$dst"
fi
echo "[restore] done."
