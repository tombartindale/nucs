# Remote runners

A runner is another machine that takes heavy jobs off the server, the way self-hosted CI
runners work. Today that means compose. A powerful workstation can run several compose
jobs at once, while the server stays a small 2 vCPU / 2 GB host.

- The runner only makes **outbound** HTTPS requests to the server, so it needs no open port.
- It authenticates with its own token.
- It reaches video through the media bucket, using temporary credentials the server issues for each job. It holds no AWS keys of its own.
- It never touches Postgres, Redis or the programme volume.

```
 server (api)                                        runner (your machine)
 ─────────────                                       ─────────────────────
 compose queued, pool=remote  ◄── POST /api/runner/claim (long-poll) ──
 bcn bundle create → s3://…/_jobs/<id>/in.tar.gz
                              ── job, presigned URLs, ──►  fetch in.tar.gz, extract
                                 temporary S3 credentials
                                                           bcn compose  (reads master from S3,
                              ◄── events (progress) ────    uploads composed.mp4 to S3)
                                  reply: {cancel}          bcn bundle collect → PUT out.tar.gz
 bcn bundle apply out.tar.gz  ◄── complete ─────────────
 (pointer, compose.json, srt written; stamped at snapshot time)
```

Only the outputs a command is allowed to write are accepted back. For compose these are:
- `build/compose*.json`;
- the composed/draft pointers;
- the delivery SRT.

Anything else rejects the whole archive. If someone edits a topic while the runner is working on it, compose correctly shows as **stale** once the result comes back.

## 1. Bucket and credentials

The runner machine holds **no AWS keys**. For each job it claims, the server mints temporary credentials using `sts:GetFederationToken`, federated from the server's own IAM user, and sends them with the job. Each set of credentials:
- is limited by a session policy to that job's topic: it can read anything under `media/<module>/<unit>/<topic>/` and write only that topic's `build/`;
- can never do more than the server's own policy allows;
- expires after 12 hours.

The runner passes only these credentials to bcn and drops any `AWS_*` settings of its own. For the two hand-off archives in `_jobs/` it uses presigned URLs instead.

1. Re-run `deploy/media/setup-bucket.sh`. It adds a lifecycle rule that expires any `_jobs/` archives a crash left behind.
2. Add one statement to the **server's** IAM user, the one the api uses, so it can issue these credentials. Replace `<account-id>` with your AWS account ID:

```json
{
  "Sid": "IssueRunnerCredentials",
  "Effect": "Allow",
  "Action": "sts:GetFederationToken",
  "Resource": "arn:aws:sts::<account-id>:federated-user/nucs-runner-*"
}
```

The server must run as an **IAM user** with long-term access keys. That is how it is set up today (`deploy/UPGRADE-S3-MEDIA.md`). `GetFederationToken` cannot be called with role credentials.

If you use an S3-compatible store with its own STS endpoint instead of AWS, set `S3_MEDIA_STS_ENDPOINT` on the api.

## 2. Register a runner

In the app, go to **Admin → Runners → Add a runner**. This gives a one-time registration token, valid for 24 hours, and a `docker run` line you can copy.

On the runner machine, use [`deploy/runner/`](runner/docker-compose.yml). It runs the **same image as the server**:

```sh
cp deploy/runner/.env.example deploy/runner/.env   # NUCS_URL, the token, slots
cd deploy/runner && docker compose up -d && docker compose logs -f
```

After you deploy a new server build, run `docker compose pull && docker compose up -d` there as well.

If you'd rather not use compose, this plain `docker run` is equivalent:

```sh
docker run -d --name beacon-runner --restart unless-stopped \
  -e NUCS_URL=https://nucs.example.org \
  -e RUNNER_REGISTRATION_TOKEN=<token from the app> \
  -v beacon-runner:/data/scratch \
  --entrypoint node ghcr.io/<owner>/nucs:latest \
  /app/ui/server/dist/runner.js --slots 3 --commands compose
```

On its first start the runner swaps the registration token for its own runner token. It stores that token in the volume at `/data/scratch/runner-token`, so restarts don't need a new one. To retire a runner, remove it in **Admin → Runners**. Its token stops working immediately.

The runner takes these flags. Each can also be set as an environment variable.

| Flag | Env | Default | Meaning |
| --- | --- | --- | --- |
| `--slots N` | `RUNNER_SLOTS` | 1 | Jobs at once. Each is one topic. |
| `--threads N` | `RUNNER_THREADS` | cores ÷ slots | ffmpeg threads per job, so slots don't fight over cores. |
| `--commands a,b` | `RUNNER_COMMANDS` | any | Only take these commands. |
| `--labels a,b` | `RUNNER_LABELS` | none | Labels the server can require (see below). |
| `--name X` | `RUNNER_NAME` | hostname | Name shown in the app (used at registration). |

**Sizing.** A full compose at 1080p uses roughly 1–1.5 GB of RAM and as much CPU as it is given. For example, a 16-core / 64 GB machine is comfortable at `--slots 4`. With Docker Desktop on a Mac, set its VM's CPU and memory limits to match.

**Version.** The server refuses jobs to a runner built from a different image than its own (`BEACON_VERSION`, the commit). This is because the snapshot and output formats must match. After deploying, pull the image on the runner machine too. **Admin → Runners** flags any runner that is out of date.

## 3. Choose what runs where

Use **Admin → Runners → Where jobs run**. Only the commands that bcn can carry to a runner are listed (see `bcn bundle <root> specs`).

| Setting | Meaning |
| --- | --- |
| This server | The server's own worker runs the command (the old behaviour). |
| A runner when one is online | **Default for compose.** Use a runner if one that takes the command has been seen in the last minute; otherwise run locally. |
| Runners only | The command always waits for a runner. |

**Required labels** narrow which runners a command goes to. For example, require `heavy` for compose and start only your big machine with `--labels heavy`.

Bumpers and render can also run remotely. Switch them on in the same place. No other change is needed, though compose is the only one exercised end to end so far.

**Unit and module runs.** A compose sent to runners for a whole unit or module is split into one job per topic. That way several runners, or several slots, work through it in parallel.

## What happens when something goes wrong

- **The runner dies or loses its network.** The runner reports in about every second. If the server hears nothing for 30 seconds, it marks the job **interrupted**, as it would a crashed local job. A job the runner never acknowledged goes back in the queue for another runner instead. The server's worker checks this every 15 seconds.
- **Cancel.** The next report from the runner gets the cancel, and it sends bcn SIGINT. bcn then cleans up as it does locally.
- **The video is on local disk, not in the bucket.** The job fails at once with `BUNDLE_SNAPSHOT` and a hint to run `bcn media … migrate`. A runner can only reach video in the bucket.
- **The outputs don't fit what compose writes.** `BUNDLE_REJECTED`: nothing is written, and the job fails with that envelope attached.
