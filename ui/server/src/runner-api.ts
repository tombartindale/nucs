// Remote runners: machines outside this server that take the jobs routed to them
// (Prefs.routing), the way a CI system's self-hosted runners work. A runner only ever makes
// outbound HTTPS requests to these routes with its own bearer token; it never touches
// Postgres, Redis or the programme volume.
//
//   POST /api/runner/register          one-time registration token -> runner token
//   POST /api/runner/claim             long-poll for a job; 204 when there is none
//   POST /api/runner/jobs/:id/events   bcn's stderr lines; keeps the lease alive; replies {cancel}
//   POST /api/runner/jobs/:id/complete exit code, envelope, log; applies the runner's outputs
//
// A job's files travel through the media bucket, never through this server's HTTP: claim
// snapshots the job's inputs (bcn bundle create) to <prefix>/_jobs/<id>/in.tar.gz and hands
// the runner presigned URLs to fetch that and to upload out.tar.gz; complete checks and
// writes the outputs (bcn bundle apply). Video itself never leaves the bucket (see
// tooling/bcn/commands/bundle.py); the runner's bcn reaches it with temporary credentials
// minted per job and limited to that job's objects (MediaStore.jobCredentials), so a runner
// machine holds no AWS keys of its own.
import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import type { AnyEnvelope, RunnerInfo, RunnersResponse, RunnerTokenResponse } from '@beacon/shared';
import type { FastifyInstance, FastifyRequest } from 'fastify';
import { RUNNER_ONLINE_MS, runnerTakes, type App } from './app.js';
import { parseEvent } from './bcn-invoke.js';
import type { JobRow, RunnerRow } from './db.js';
import { claimNext, finishJob, leaseUntil, reapExpiredLeases } from './dispatch.js';
import { BadRequest } from './errors.js';
import { summaryFromRow } from './jobs.js';
import type { MediaStore } from './media-store.js';
import { now, pyJson } from './util.js';

export const RUNNER_PATH = '/api/runner/';
/** How long a runner has to report in (events or complete) before its job is reclaimed. */
export const LEASE_MS = 30_000;
const CLAIM_WAIT_MS = 25_000;
const APPLY_LEASE_MS = 10 * 60_000;
const REGISTRATION_TTL_MS = 24 * 3600_000;
const MAX_LINES = 2000;
/** Built into the image (Dockerfile, BEACON_VERSION). A runner on a different build is refused
 *  jobs: its bcn might snapshot or write outputs differently. 'dev' turns the check off. */
export const SERVER_VERSION = process.env.BEACON_VERSION || 'dev';

class HttpError extends Error {
  constructor(readonly statusCode: number, message: string) { super(message); }
}

const hash = (token: string) => createHash('sha256').update(token).digest('hex');
const NAME_RE = /^[A-Za-z0-9 ._-]{1,60}$/;
const LABEL_RE = /^[a-z0-9][a-z0-9_-]{0,30}$/;

function list(value: unknown, re: RegExp, what: string): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 20 || !value.every((v) => typeof v === 'string' && re.test(v))) {
    throw new BadRequest(`'${what}' must be a list of short lowercase names.`);
  }
  return [...new Set(value as string[])];
}

export function runnerInfo(r: RunnerRow, running = 0): RunnerInfo {
  return {
    id: r.id, name: r.name, labels: JSON.parse(r.labels), commands: JSON.parse(r.commands), slots: r.slots, version: r.version,
    created: r.created, last_seen: r.last_seen, running,
    online: !!r.last_seen && Date.now() - Date.parse(r.last_seen) < RUNNER_ONLINE_MS,
  };
}

type ReadJson = (req: FastifyRequest) => Promise<Record<string, unknown>>;

export function registerRunnerRoutes(f: FastifyInstance, app: App, media: MediaStore, readJson: ReadJson,
  requireAdmin: (req: FastifyRequest) => void): void {
  const db = app.db;
  const work = join(app.dataDir, 'runner-jobs');

  async function authenticate(req: FastifyRequest): Promise<RunnerRow> {
    const m = /^Bearer (bcr_[A-Za-z0-9_-]{20,100})$/.exec(String(req.headers.authorization || ''));
    if (!m) throw new HttpError(401, 'A runner token is required.');
    const res = await db.pool.query<RunnerRow>(
      'UPDATE runners SET last_seen=$1 WHERE token_hash=$2 AND NOT revoked RETURNING *', [now(), hash(m[1])]);
    if (!res.rows[0]) throw new HttpError(401, 'This runner token is not valid, or the runner was removed.');
    return res.rows[0];
  }

  /** A running job this runner holds; anything else (reclaimed, cancelled, another runner's) is refused. */
  async function heldJob(req: FastifyRequest<{ Params: Record<string, string> }>, runner: RunnerRow): Promise<JobRow> {
    const res = await db.pool.query<JobRow>(
      "SELECT * FROM jobs WHERE id=$1 AND state='running' AND pool='remote' AND runner_id=$2", [Number(req.params.id), runner.id]);
    if (!res.rows[0]) throw new HttpError(409, 'This runner no longer holds that job.');
    return res.rows[0];
  }

  /** Resolves when a job is queued (job-wake) or after `ms`, whichever comes first. */
  function nextWake(ms: number): Promise<void> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => { off(); resolve(); }, ms);
      const off = app.bus.subscribe((ev) => {
        if (ev.type !== 'job-wake') return;
        clearTimeout(timer);
        off();
        resolve();
      });
    });
  }

  /** Snapshots a claimed job's inputs to the bucket and builds what the runner needs. Null
   *  (and the job failed, with bcn's envelope saying why) when the snapshot cannot be made. */
  async function prepare(row: JobRow): Promise<Record<string, unknown> | null> {
    mkdirSync(work, { recursive: true });
    const dir = mkdtempSync(join(work, `job-${row.id}-`));
    const target = (JSON.parse(row.targets) as string[])[0];
    const fail = (envelopes: AnyEnvelope[], message: string) => finishJob(db, app.bus, row.id, {
      state: 'failed', exitCode: null, durationMs: 0, envelopes,
      log: [pyJson({ event: 'log', level: 'error', message })],
    });
    try {
      const archive = join(dir, 'in.tar.gz');
      const env = await app.bcn.query<{ ok: boolean; stamp?: number }>(
        'bundle', [app.targetPath(target), 'create', '--cmd', row.command, '--archive', archive]);
      if (!env.ok || typeof env.stamp !== 'number') {
        await fail([env as unknown as AnyEnvelope], 'This job could not be sent to a runner.');
        return null;
      }
      await media.putFile(media.jobKey(row.id, 'in.tar.gz'), archive);
      await db.pool.query('UPDATE jobs SET snapshot_stamp=$1 WHERE id=$2', [env.stamp, row.id]);
      return {
        job_id: row.id, command: row.command, target, args: JSON.parse(row.args), label: row.label, stamp: env.stamp,
        snapshot_url: await media.presignObject(media.jobKey(row.id, 'in.tar.gz'), 'get'),
        output_url: await media.presignObject(media.jobKey(row.id, 'out.tar.gz'), 'put'),
        lease_ms: LEASE_MS,
        credentials: await media.jobCredentials(row.id, target),
        storage: {
          bucket: media.bucket, prefix: media.prefix,
          region: process.env.S3_MEDIA_REGION || process.env.AWS_DEFAULT_REGION || '',
          endpoint: process.env.S3_MEDIA_ENDPOINT || '',
        },
      };
    } catch (e) {
      await fail([], `This job could not be sent to a runner: ${(e as Error).message}`);
      return null;
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }

  // -- the runner's side ------------------------------------------------------------------------

  f.post('/api/runner/register', async (req) => {
    const data = await readJson(req);
    const name = String(data.name ?? '');
    if (!NAME_RE.test(name)) throw new BadRequest('A runner needs a short name (letters, digits, spaces, . _ -).');
    const res = await db.pool.query('DELETE FROM runner_registration_tokens WHERE token_hash=$1 AND expires > $2 RETURNING token_hash',
      [hash(String(data.registration_token ?? '')), now()]);
    if (!res.rows[0]) throw new HttpError(401, 'That registration token is not valid, has expired, or was already used.');
    const token = `bcr_${randomBytes(32).toString('base64url')}`;
    const id = randomUUID();
    await db.pool.query('INSERT INTO runners(id, name, token_hash, created) VALUES($1, $2, $3, $4)', [id, name, hash(token), now()]);
    return { runner_id: id, runner_token: token };
  });

  f.post('/api/runner/claim', async (req, reply) => {
    let runner = await authenticate(req);
    const data = await readJson(req);
    // What the runner takes is whatever it was last started with, so changing its flags
    // never needs a new registration.
    const labels = list(data.labels, LABEL_RE, 'labels');
    const commands = list(data.commands, LABEL_RE, 'commands');
    const slots = Math.max(1, Math.min(64, Math.trunc(Number(data.slots) || 1)));
    const version = String(data.version ?? '').slice(0, 80);
    runner = (await db.pool.query<RunnerRow>('UPDATE runners SET labels=$1, commands=$2, slots=$3, version=$4 WHERE id=$5 RETURNING *',
      [JSON.stringify(labels), JSON.stringify(commands), slots, version, runner.id])).rows[0];
    if (SERVER_VERSION !== 'dev' && version !== SERVER_VERSION) {
      throw new HttpError(409, `This runner is version ${version || 'unknown'} but the server is ${SERVER_VERSION}; update the runner's image.`);
    }
    if (!media.enabled) throw new HttpError(503, 'Object storage is not configured on this server, so it cannot hand jobs to runners.');
    const deadline = Date.now() + CLAIM_WAIT_MS;
    let gone = false;
    req.raw.once('close', () => { gone = true; });
    while (!gone && Date.now() < deadline) {
      await reapExpiredLeases(db, app.bus);
      const row = await claimNext(db, {
        pool: 'remote', id: runner.id, runnerName: runner.name, leaseMs: LEASE_MS,
        accept: (r) => runnerTakes(runner, r.command, JSON.parse(r.requires || '[]')),
      });
      if (row) {
        app.bus.publish({ type: 'job', job: summaryFromRow(row) });
        const spec = await prepare(row);
        if (spec) return spec;
        continue;
      }
      await nextWake(Math.min(5000, deadline - Date.now()));
    }
    return reply.code(204).send();
  });

  f.post('/api/runner/jobs/:id(^\\d+$)/events', async (req: FastifyRequest<{ Params: Record<string, string> }>) => {
    const runner = await authenticate(req);
    const row = await heldJob(req, runner);
    const data = await readJson(req);
    const lines = (Array.isArray(data.lines) ? data.lines : []).slice(0, MAX_LINES).map(String).filter(Boolean);
    let progress: string | null = null;
    for (const line of lines) {
      const ev = parseEvent(line);
      if (ev.event === 'progress') progress = JSON.stringify(ev);
      app.bus.publish({ type: 'job-event', job: row.id, target_index: 0, target_count: 1, event: ev });
    }
    await db.pool.query('UPDATE jobs SET lease_expires_at=$1, last_event=$2, progress=COALESCE($3, progress) WHERE id=$4',
      [leaseUntil(LEASE_MS), now(), progress, row.id]);
    return { cancel: row.cancel_requested };
  });

  f.post('/api/runner/jobs/:id(^\\d+$)/complete', async (req: FastifyRequest<{ Params: Record<string, string> }>) => {
    const runner = await authenticate(req);
    const row = await heldJob(req, runner);
    const data = await readJson(req);
    // Applying can take a while; the runner has finished, so it must not be reaped meanwhile.
    await db.pool.query('UPDATE jobs SET lease_expires_at=$1 WHERE id=$2', [leaseUntil(APPLY_LEASE_MS), row.id]);
    const envelopes: AnyEnvelope[] = data.envelope && typeof data.envelope === 'object' ? [data.envelope as AnyEnvelope] : [];
    const log = (Array.isArray(data.log) ? data.log : []).slice(-MAX_LINES).map(String);
    const exitCode = typeof data.exit_code === 'number' ? Math.trunc(data.exit_code) : null;
    let applied = true;
    if (data.outputs === true) {
      mkdirSync(work, { recursive: true });
      const dir = mkdtempSync(join(work, `job-${row.id}-`));
      try {
        const archive = join(dir, 'out.tar.gz');
        await media.getFile(media.jobKey(row.id, 'out.tar.gz'), archive);
        const target = (JSON.parse(row.targets) as string[])[0];
        const env = await app.bcn.query<{ ok: boolean; written?: string[] }>('bundle', [app.targetPath(target), 'apply',
          '--cmd', row.command, '--archive', archive, '--stamp', String(row.snapshot_stamp)]);
        applied = env.ok;
        if (!applied) envelopes.push(env as unknown as AnyEnvelope);
        log.push(pyJson({ event: 'log', level: applied ? 'info' : 'error',
          message: applied ? `applied ${env.written?.length ?? 0} file(s) from ${runner.name}` : `the results from ${runner.name} were not applied` }));
      } catch (e) {
        applied = false;
        log.push(pyJson({ event: 'log', level: 'error', message: `the results from ${runner.name} could not be fetched: ${(e as Error).message}` }));
      } finally {
        rmSync(dir, { recursive: true, force: true });
      }
    }
    const state = data.interrupted === true ? 'interrupted'
      : (row.cancel_requested || data.cancelled === true) ? 'cancelled'
        : exitCode === 0 && applied ? 'done' : 'failed';
    const started = row.started ? Date.parse(row.started) : Date.now();
    await finishJob(db, app.bus, row.id, { state, exitCode, durationMs: Date.now() - started, envelopes, log });
    await media.deleteKeys([media.jobKey(row.id, 'in.tar.gz'), media.jobKey(row.id, 'out.tar.gz')]).catch(() => undefined);
    return { state };
  });

  // -- administration (session and admin required, like every other /api/admin route) ----------

  f.get('/api/admin/runners', async (req): Promise<RunnersResponse> => {
    requireAdmin(req);
    const rows = await db.pool.query<RunnerRow>('SELECT * FROM runners WHERE NOT revoked ORDER BY created');
    const busy = await db.pool.query<{ runner_id: string; n: number }>(
      "SELECT runner_id, count(*)::int AS n FROM jobs WHERE state='running' AND pool='remote' GROUP BY runner_id");
    const running = new Map(busy.rows.map((r) => [r.runner_id, r.n]));
    return { runners: rows.rows.map((r) => runnerInfo(r, running.get(r.id) ?? 0)), remote_commands: app.remoteCommands,
      server_version: SERVER_VERSION };
  });

  f.post('/api/admin/runners/tokens', async (req): Promise<RunnerTokenResponse> => {
    requireAdmin(req);
    const token = randomBytes(24).toString('base64url');
    const expires = leaseUntil(REGISTRATION_TTL_MS);
    await db.pool.query('INSERT INTO runner_registration_tokens(token_hash, created, expires, created_by) VALUES($1, $2, $3, $4)',
      [hash(token), now(), expires, String((req as FastifyRequest & { email?: string }).email ?? '')]);
    return { token, expires };
  });

  f.delete('/api/admin/runners/:id', async (req: FastifyRequest<{ Params: Record<string, string> }>, reply) => {
    requireAdmin(req);
    // A removed runner's token stops working at once; any job it holds is reclaimed when its lease runs out.
    await db.pool.query('UPDATE runners SET revoked=true WHERE id=$1', [req.params.id]);
    return reply.code(204).send();
  });
}
