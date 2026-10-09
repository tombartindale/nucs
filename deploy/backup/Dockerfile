# Built from the repo root (see docker-compose.yml). Runs backup.sh nightly, mirroring the
# programme volume straight to S3 via `aws s3 sync` -- no bcn invocation in this image at all.
# The Postgres client comes from the base image; jq drives restore-programme.sh --as-of's
# manifest JSON iteration.
FROM postgres:17-alpine

RUN apk add --no-cache aws-cli dcron jq

COPY deploy/backup/backup.sh deploy/backup/restore.sh deploy/backup/restore-programme.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh /usr/local/bin/restore-programme.sh

COPY deploy/backup/crontab /etc/crontabs/root

CMD ["crond", "-f", "-l", "2"]
