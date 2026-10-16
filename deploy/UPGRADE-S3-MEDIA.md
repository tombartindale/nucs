# Upgrade: video in S3

This moves master video and the composed, draft and delivered mp4s off the `programme-root` volume and into an S3 bucket. The volume then only holds small files (text, JSON, slide images, pointer files). How it works is in the README under "Video in object storage".

**Read the whole thing before starting.** Moving video over is the one step that deletes data from the volume. The order below is chosen so that the disk is never needed for anything it cannot currently do.

## 0. What you need

- Admin AWS credentials for one-time bucket setup (not the app's own credentials).
- Shell access to the server, in the directory with `docker-compose.yml` and `.env`.
- The public `https://` origin the app is served from (for example `https://beacon.example.edu`).
- A maintenance window of an hour or two for the migration, with nobody composing or uploading.

## 1. AWS: bucket and permissions

### 1a. Create the media bucket

Create a **new, separate** bucket for media (for example `beacon-media-prod`), in the same region as the server, with default settings. Keep it separate from the backup bucket because its lifecycle rules and importance differ: this bucket becomes the only copy of the video.

### 1b. Run the one-time setup script (admin credentials)

The admin user needs `s3:PutBucketPublicAccessBlock`, `s3:PutBucketVersioning`, `s3:PutBucketCORS` and `s3:PutLifecycleConfiguration` on the media bucket.

```sh
S3_MEDIA_BUCKET=beacon-media-prod \
APP_ORIGIN=https://beacon.example.edu \
S3_MEDIA_PREFIX=media \
MEDIA_NONCURRENT_DAYS=90 \
./deploy/media/setup-bucket.sh
```

This blocks public access, turns on versioning, sets the CORS rule browser uploads need, and expires abandoned uploads. It is safe to re-run. `MEDIA_NONCURRENT_DAYS` is how long a replaced or deleted video stays recoverable.

### 1c. Update the app's IAM permissions

You are using the same IAM user as the backups. It already has permissions on the backup bucket. **Add** a second statement set for the media bucket; leave the backup statements alone.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "MediaListBucket",
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:ListBucketMultipartUploads"],
      "Resource": "arn:aws:s3:::beacon-media-prod"
    },
    {
      "Sid": "MediaObjects",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:GetObjectVersion",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": "arn:aws:s3:::beacon-media-prod/media/*"
    }
  ]
}
```

If you changed `S3_MEDIA_PREFIX`, change `media/*` to match. Why each permission is needed:

| Permission | Used for |
| --- | --- |
| `s3:PutObject` | Uploading compose outputs and migrated videos; the browser's presigned part uploads and finishing them; server-side copies (the destination side). |
| `s3:GetObject` | Playback (presigned URLs), compose downloading a master, server-side copies (the source side), checking the first bytes of an upload. |
| `s3:GetObjectVersion` | Compose downloads the exact version the pointer records. Without it, downloads fail with 403. |
| `s3:DeleteObject` | Removing an upload from staging once it has been moved into place. |
| `s3:AbortMultipartUpload`, `s3:ListMultipartUploadParts` | Cleaning up an upload that failed or was cancelled in the browser. |
| `s3:ListBucket` (on the bucket) | **Required, not optional.** Without it S3 answers a lookup for a missing object with 403 instead of 404, and bcn treats that as an error rather than "not there". |
| `s3:ListBucketMultipartUploads` | Tidying incomplete uploads. |

No bucket-level admin permissions are needed by the app, and it should not be given them. Do **not** give the app `s3:PutBucketVersioning`, `s3:PutBucketPolicy` and similar: the setup script is the only thing that needs them, and it runs with your admin credentials.

If you would rather use a separate IAM user for media, put those two statements on that user instead, and set `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` for the api and worker to it. The backup container reads the same variables, so it would then also need its own: that is why using one user is simpler.

## 2. Free some disk before anything else

The volume is full, so first make room for the upgrade itself (pulling an image, writing logs, new pointer files). On the **currently running** version nothing in this step needs the new code, but the command below is part of the new code, so if the disk is too full to pull the new image, do the manual clean in 2a first.

### 2a. Manual clean (works on the old version)

Old browser uploads are copied into `transfer/incoming/media-*` and were never deleted. They are usually a full duplicate of each uploaded video.

```sh
docker compose exec worker sh -c 'ls -d /data/programme/transfer/incoming/media-* | head; du -sh /data/programme/transfer/incoming /data/programme/transfer/exports'
```

Check the list, then delete the ones older than a few days, once you have confirmed each video is in place as `<topic>/edit/master.mp4`:

```sh
docker compose exec worker find /data/programme/transfer/incoming -maxdepth 1 -name 'media-*' -mtime +3 -print
# looks right? then:
docker compose exec worker find /data/programme/transfer/incoming -maxdepth 1 -name 'media-*' -mtime +3 -exec rm -rf {} +
```

Old exports in `transfer/exports/` are also safe to delete if you no longer need them.

### 2b. After the upgrade: the built-in clean-up

`bcn` is not on the container's PATH, so commands use its full path, `/app/tooling/.venv/bin/bcn`.

```sh
docker compose exec worker /app/tooling/.venv/bin/bcn media /data/programme gc            # shows what it would remove
docker compose exec worker /app/tooling/.venv/bin/bcn media /data/programme gc --apply    # removes it
```

It removes upload folders, aborted import folders and compose scratch directories older than 24 hours (`--hours N` to change).

## 3. Deploy the new version

1. Back up first. Confirm last night's backup ran: check that `s3://<backup bucket>/<prefix>/programme/` and `db/` have recent objects. As an extra safety net, run `docker compose exec backup /usr/local/bin/backup.sh` (or your usual manual trigger) now.
2. Edit `.env` (see `.env.example`):

   ```
   S3_MEDIA_BUCKET=beacon-media-prod
   S3_MEDIA_PREFIX=media
   S3_MEDIA_REGION=eu-west-2        # the bucket's region; blank uses AWS_DEFAULT_REGION
   ```

   `AWS_*` stay as they are (same user).
3. Pull and restart:

   ```sh
   docker compose pull
   docker compose up -d
   ```

   This also creates the new `media-scratch` volume the workers use.
4. Check it started: `docker compose logs --tail 50 api worker`, then open the app.

At this point the system runs in **mixed mode**. Videos still on disk keep working exactly as before. New compose outputs and new uploads already go to S3. Nothing has been deleted.

## 4. Check S3 access before migrating anything

```sh
docker compose exec worker sh -c '/app/tooling/.venv/bin/python - <<EOF
import os, boto3
s3 = boto3.client("s3")
b, p = os.environ["S3_MEDIA_BUCKET"], os.environ["S3_MEDIA_PREFIX"]
s3.put_object(Bucket=b, Key=f"{p}/_incoming/probe.txt", Body=b"x")
print("put ok")
s3.head_object(Bucket=b, Key=f"{p}/_incoming/probe.txt"); print("head ok")
try:
    s3.head_object(Bucket=b, Key=f"{p}/_incoming/does-not-exist")
except Exception as e:
    print("missing ->", e.response["Error"]["Code"], "(want 404)")
s3.delete_object(Bucket=b, Key=f"{p}/_incoming/probe.txt"); print("delete ok")
EOF'
```

Everything should print `ok`, and the missing-object check must say `404`. A `403` there means `s3:ListBucket` is missing from the policy.

## 5. Migrate existing video from the full drive to S3

Do this in a quiet period: no uploads, no compose jobs. Cancel or wait for anything running under Jobs first.

### 5a. See what will happen

```sh
docker compose exec worker /app/tooling/.venv/bin/bcn media /data/programme migrate --human
```

Without `--apply` this only reports: every file that would be uploaded and the total size. Check the total against the free space you have in S3 and the size you expect.

### 5b. Try one module with `--apply` but without `--delete`

This uploads and writes pointers but keeps the local files, so it needs no free disk, only network and S3 space.

```sh
docker compose exec worker /app/tooling/.venv/bin/bcn media /data/programme/KV7015 migrate --apply
docker compose exec worker /app/tooling/.venv/bin/bcn status /data/programme/KV7015 --verify --human
```

`--verify` checks each video in S3 still exists at the recorded size and is an MP4. Then, in the app, open a topic from that module: the video should play, seeking should work, and the cue and status tiles should be unchanged (not stale). If everything looks right, you have proved the whole path.

### 5c. Migrate and free the disk, module by module

```sh
docker compose exec worker /app/tooling/.venv/bin/bcn media /data/programme/KV7015 migrate --apply --delete
docker compose exec worker /app/tooling/.venv/bin/bcn status /data/programme/KV7015 --verify --human
docker compose exec worker df -h /data/programme
```

`--delete` removes each local file only after its upload has been verified (the object's size must match), and works one file at a time, so disk frees as it goes. If it is interrupted, re-run the same command: files already moved are skipped (they are pointers now) and the rest continue.

Repeat for each module, **starting with the ones using the most space**. If the disk is so full that even small writes fail, do the largest module first with `--apply --delete`; the first deleted file frees room for the pointer files that follow.

When all modules are done:

```sh
docker compose exec worker /app/tooling/.venv/bin/bcn media /data/programme migrate --human     # should list nothing to upload
docker compose exec worker /app/tooling/.venv/bin/bcn status /data/programme --verify --human
docker compose exec worker /app/tooling/.venv/bin/bcn media /data/programme gc --apply
docker compose exec worker df -h /data/programme
```

### 5d. Safety net, and when it expires

Until you do step 5c, every video exists twice (disk and S3). After it, the S3 media bucket is the **only copy**, with these protections:

- Versioning on the bucket: a replaced or deleted video is kept for `MEDIA_NONCURRENT_DAYS` (90 by default).
- The nightly backup mirror in the backup bucket still holds every master it backed up before the migration. The backup will now see the masters gone from disk and, because it syncs with `--delete`, it marks them deleted (a delete marker; the old content stays as a noncurrent version) and expires them after `BACKUP_RETENTION_DAYS` (14 by default). So that old backup copy is a short-term extra safety net, not a long-term one. Treat the media bucket's versioning as the real protection.
- Pointer files are backed up nightly (they are small JSON files in the same programme mirror).

## 6. Day-to-day after the migration

- Uploads: the topic's Video upload in the UI now sends the file straight to S3 in 64 MB parts, three at a time, with progress. A failed or cancelled upload is aborted so no parts are left billing.
- Compose: the worker downloads that topic's master into `/data/scratch` (the `media-scratch` volume), composes, uploads the result, and deletes the download. Space needed: one master per concurrent compose in `media-scratch`.
- **The compose work directory is still on the programme volume**, under `<topic>/build/.compose-*`, while a compose runs: it holds the encoded output before upload. Keep free space for roughly the size of one composed video per concurrent compose (two workers by default). It is removed when the job ends.
- Downloading or exporting: `bcn transfer --export --media` fetches each video from S3 into the zip one at a time, so the zip is the only large thing on disk.
- Bulk-copying video elsewhere with plain file tools will not work any more; the files are in the bucket, not on the volume.

## 7. If something goes wrong

| Symptom | Likely cause and fix |
| --- | --- |
| Upload in the UI fails immediately with a CORS or network error | The CORS rule is missing or `APP_ORIGIN` was wrong. Re-run `setup-bucket.sh` with the correct origin (no trailing slash). |
| Upload reaches 100% then says the receipt was not exposed | CORS is missing `ExposeHeaders: ETag`. Re-run the setup script. |
| Video does not play, `503` from `/files/...` | The api has no `S3_MEDIA_BUCKET`. Check `docker compose exec api printenv S3_MEDIA_BUCKET` and restart. |
| Video does not play, redirects to an S3 error page (`AccessDenied`) | The policy lacks `s3:GetObject` on the prefix, or the IAM user's credentials in the api are different from what you tested. |
| Compose fails with `403` / `Forbidden` on download | Missing `s3:GetObjectVersion` (or `s3:ListBucket`). |
| `bcn ... FS_MISSING ... is recorded as in the bucket but the object is missing` | The object was deleted from S3. Restore the previous version from the bucket's version history (S3 console, "Show versions", delete the delete marker), or re-upload the video. |
| Disk filling up again | Run `bcn media /data/programme gc`. Check `docker compose exec worker du -sh /data/scratch /data/programme/*/*/*/build` for leftover `.scratch-*` or `.compose-*` after a killed worker. |
| `bcn: boto3 is not installed` | The image was not rebuilt with the S3 extra. Pull the new image (`docker compose pull`), do not run an old one. |

### Rolling back

Because migration only adds pointers and then removes local files, a rollback means putting the videos back on disk:

1. Clear `S3_MEDIA_BUCKET` in `.env` and restart: the app then looks for local files again. Videos still on disk work; migrated ones do not play.
2. For each migrated video, download it back and remove the pointer, for example:

   ```sh
   aws s3 cp s3://beacon-media-prod/media/KV7015/U01/T01/edit/master.mp4 /data/programme/KV7015/U01/T01/edit/master.mp4
   rm /data/programme/KV7015/U01/T01/edit/master.mp4.s3.json
   ```

   This needs the disk space that caused the problem, so rolling back is only realistic for a handful of files. Fixing forward is almost always better.

## 8. Checklist

- [ ] Media bucket created, `setup-bucket.sh` run with the correct `APP_ORIGIN`
- [ ] IAM user updated with the media statements (section 1c)
- [ ] Last night's backup confirmed
- [ ] Old `transfer/incoming/media-*` folders cleaned
- [ ] `.env` updated, new image pulled, stack restarted
- [ ] S3 access probe (section 4) all `ok`, missing object returns `404`
- [ ] One module migrated with `--apply` and checked (plays, not stale, `status --verify` clean)
- [ ] All modules migrated with `--apply --delete`; `df` shows the space back
- [ ] `bcn media ... gc --apply` run; no leftovers
- [ ] A test upload through the UI works and plays
- [ ] A test compose works and `/data/scratch` is empty afterwards
