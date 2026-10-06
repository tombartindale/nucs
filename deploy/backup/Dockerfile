# Built from the repo root (see docker-compose.yml) so it can run bcn against the programme
# volume for the nightly full export. The Postgres client comes from the base image.
FROM postgres:17-alpine

RUN apk add --no-cache aws-cli dcron python3

WORKDIR /app
COPY tooling/pyproject.toml tooling/pyproject.toml
COPY tooling/bcn tooling/bcn
RUN python3 -m venv tooling/.venv && tooling/.venv/bin/pip install --no-cache-dir -q -e tooling

COPY deploy/backup/backup.sh deploy/backup/restore.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/restore.sh

COPY deploy/backup/crontab /etc/crontabs/root

CMD ["crond", "-f", "-l", "2"]
