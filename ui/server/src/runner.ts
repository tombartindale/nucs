// A remote runner: takes the jobs this server routes to runners (Settings > Runners), runs
// bcn for them on this machine, and reports back — like a CI system's self-hosted runner.
// It only makes outbound HTTPS requests to the server (runner-api.ts) and reaches video
// through the media bucket with temporary credentials the server issues for each job; it
// needs no AWS keys, database, Redis or programme volume. Run it from the same image as the server, so bcn, ffmpeg and Chrome are
// exactly the versions the server's own worker uses (see deploy/RUNNER.md):
//
//   node /app/ui/server/dist/runner.js [--slots N] [--labels a,b] [--commands compose] [--threads N] [--name NAME]
//
// Environment: NUCS_URL (required); RUNNER_TOKEN, or RUNNER_REGISTRATION_TOKEN on first
// start (the runner token it receives is kept in $BCN_SCRATCH/runner-token); BCN_SCRATCH
// (default /data/scratch) for job workspaces.
import { spawn } from 'node:child_process';
import { chmodSync, createWriteStream, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { cpus, hostname } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { ReadableStream } from 'node:stream/web';
import type { AnyEnvelope, JobArgs } from '@beacon/shared';
import { bcnArgv, invokeBcn } from './bcn-invoke.js';
import { locateBcn } from './bcn.js';
import { LOG_LINES } from './dispatch.js';
import { pyJson, sleep } from './util.js';

const CLAIM_TIMEOUT_MS = 40_000;   // the server holds a claim open for up to 25 s
const FLUSH_MS = 1000;
const RETRY_MS = 5000;
const REFUSED_MS = 30_000;

interface Spec {
  job_id: number; command: string; target: string; args: JobArgs; label: string; stamp: number;
  snapshot_url: string; output_url: string; lease_ms: number;
  credentials: { access_key_id: string; secret_access_key: string; session_token: string; expiration: string };
  storage: { bucket: string; prefix: string; region: string; endpoint: string };
}

interface Options {
  url: string; name: string; slots: number; labels: string[]; commands: string[]; threads: number;
  scratch: string; version: string; bcn: string[];
}

function parseOptions(argv: string[], env: NodeJS.ProcessEnv): Options {
  const flag = (name: string): string | undefined => {
    const i = argv.indexOf(`--${name}`);
    return i >= 0 ? argv[i + 1] : undefined;
  };
  const csv = (v: string | undefined) => (v ? v.split(',').map((x) => x.trim()).filter(Boolean) : []);
  const url = (env.NUCS_URL || '').replace(/\/+$/, '');
  if (!/^https?:\/\//.test(url)) throw new Error('NUCS_URL must be the server\'s address, e.g. https://nucs.example.org');
  const slots = Math.max(1, Math.trunc(Number(flag('slots') || env.RUNNER_SLOTS || 1)) || 1);
  return {
    url, slots,
    name: flag('name') || env.RUNNER_NAME || hostname(),
    labels: csv(flag('labels') || env.RUNNER_LABELS),
    commands: csv(flag('commands') || env.RUNNER_COMMANDS),
    // Each slot's ffmpeg gets its share of the cores rather than all of them (bcn/media.py).
    threads: Math.max(1, Math.trunc(Number(flag('threads') || env.RUNNER_THREADS || 0)) || Math.floor(cpus().length / slots) || 1),
    scratch: env.BCN_SCRATCH || '/data/scratch',
    version: env.BEACON_VERSION || 'dev',
    bcn: locateBcn(env.BCN),
  };
}

class Runner {
  private token = '';
  private stopping = false;
  private procs = new Set<import('node:child_process').ChildProcess>();

  constructor(private o: Options) {}

  private log(message: string): void { process.stderr.write(`${new Date().toISOString()} ${message}\n`); }

  private async api(path: string, body: unknown, timeoutMs = 30_000): Promise<{ status: number; json: Record<string, unknown> }> {
    const res = await fetch(`${this.o.url}${path}`, {
      method: 'POST', body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs),
      headers: { 'content-type': 'application/json', ...(this.token ? { authorization: `Bearer ${this.token}` } : {}) },
    });
    const text = await res.text();
    let json: Record<string, unknown> = {};
    try { json = text ? JSON.parse(text) : {}; } catch { json = { error: text.slice(0, 200) }; }
    return { status: res.status, json };
  }

  /** RUNNER_TOKEN, else the token saved from an earlier registration, else register now. */
  async authenticate(env: NodeJS.ProcessEnv): Promise<void> {
    const saved = join(this.o.scratch, 'runner-token');
    if (env.RUNNER_TOKEN) { this.token = env.RUNNER_TOKEN; return; }
    if (existsSync(saved)) { this.token = readFileSync(saved, 'utf8').trim(); return; }
    if (!env.RUNNER_REGISTRATION_TOKEN) throw new Error('Set RUNNER_REGISTRATION_TOKEN (from Settings > Runners) to register this runner.');
    const { status, json } = await this.api('/api/runner/register', { registration_token: env.RUNNER_REGISTRATION_TOKEN, name: this.o.name });
    if (status !== 200) throw new Error(`registration refused (${status}): ${json.error ?? ''}`);
    this.token = String(json.runner_token);
    mkdirSync(this.o.scratch, { recursive: true });
    writeFileSync(saved, this.token);
    chmodSync(saved, 0o600);
    this.log(`registered as ${this.o.name} (${json.runner_id})`);
  }

  async start(): Promise<void> {
    // Workspaces a killed runner left behind; the server has already taken those jobs back.
    for (const d of existsSync(this.o.scratch) ? readdirSync(this.o.scratch) : []) {
      if (/^job-\d+$/.test(d)) rmSync(join(this.o.scratch, d), { recursive: true, force: true });
    }
    this.log(`runner ${this.o.name}: ${this.o.slots} slot(s), ${this.o.threads} thread(s) each, `
      + `labels [${this.o.labels.join(', ')}], commands [${this.o.commands.join(', ') || 'any'}], version ${this.o.version}`);
    await Promise.all(Array.from({ length: this.o.slots }, (_, i) => this.slot(i)));
  }

  stop(): void {
    this.stopping = true;
    for (const p of this.procs) if (p.exitCode === null) p.kill('SIGINT');
  }

  private async slot(n: number): Promise<void> {
    while (!this.stopping) {
      try {
        const { status, json } = await this.api('/api/runner/claim',
          { labels: this.o.labels, commands: this.o.commands, slots: this.o.slots, version: this.o.version }, CLAIM_TIMEOUT_MS);
        if (status === 204) continue;
        if (status !== 200) {
          this.log(`slot ${n}: the server refused a claim (${status}): ${json.error ?? ''}`);
          if (status === 401) { this.stopping = true; break; }
          await sleep(REFUSED_MS);
          continue;
        }
        await this.run(json as unknown as Spec);
      } catch (e) {
        if (this.stopping) break;
        this.log(`slot ${n}: ${(e as Error).message}`);
        await sleep(RETRY_MS);
      }
    }
  }

  private async run(spec: Spec): Promise<void> {
    const dir = join(this.o.scratch, `job-${spec.job_id}`);
    const root = join(dir, 'root');
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(root, { recursive: true });
    const log: string[] = [];
    let pending: string[] = [];
    let cancel = false;
    let lost = false;
    let current: import('node:child_process').ChildProcess | null = null;
    const line = (l: string) => { log.push(l); pending.push(l); };
    const note = (message: string, level = 'info') => line(pyJson({ event: 'log', level, message }));

    // Every report extends the lease; its reply says whether the job was cancelled.
    let flushing: Promise<void> | null = null;
    const flush = async () => {
      if (flushing) return flushing;
      const batch = pending;
      pending = [];
      flushing = (async () => {
        try {
          const { status, json } = await this.api(`/api/runner/jobs/${spec.job_id}/events`, { lines: batch });
          if (status === 409) { lost = true; current?.kill('SIGINT'); }
          else if (json.cancel && !cancel) { cancel = true; current?.kill('SIGINT'); }
        } catch {
          pending = [...batch, ...pending].slice(-LOG_LINES);  // try again next time
        } finally { flushing = null; }
      })();
      return flushing;
    };
    const timer = setInterval(() => void flush(), FLUSH_MS);

    let code: number | null = null;
    let envelope: AnyEnvelope | null = null;
    let outputs = false;
    try {
      this.log(`job ${spec.job_id}: ${spec.label}`);
      note(`runner ${this.o.name}: fetching the job's files`);
      await flush();
      const input = join(dir, 'in.tar.gz');
      const res = await fetch(spec.snapshot_url);
      if (!res.ok || !res.body) throw new Error(`could not fetch the job's files (${res.status})`);
      await pipeline(Readable.fromWeb(res.body as ReadableStream), createWriteStream(input));
      await this.exec('tar', ['-xzf', input, '-C', root]);

      // Only the job's own credentials: any AWS settings on this machine are dropped, so bcn
      // can never reach the bucket as anything else.
      const env: NodeJS.ProcessEnv = Object.fromEntries(Object.entries(process.env).filter(([k]) => !k.startsWith('AWS_')));
      Object.assign(env, {
        AWS_ACCESS_KEY_ID: spec.credentials.access_key_id, AWS_SECRET_ACCESS_KEY: spec.credentials.secret_access_key,
        AWS_SESSION_TOKEN: spec.credentials.session_token,
        ...(spec.storage.region ? { AWS_DEFAULT_REGION: spec.storage.region } : {}),
        S3_MEDIA_BUCKET: spec.storage.bucket, S3_MEDIA_PREFIX: spec.storage.prefix,
        S3_MEDIA_REGION: process.env.S3_MEDIA_REGION || spec.storage.region,
        BCN_SCRATCH: join(dir, 'scratch'), BCN_FFMPEG_THREADS: String(this.o.threads),
      });
      const endpoint = process.env.S3_MEDIA_ENDPOINT || spec.storage.endpoint;
      if (endpoint) env.S3_MEDIA_ENDPOINT = endpoint; else delete env.S3_MEDIA_ENDPOINT;
      mkdirSync(env.BCN_SCRATCH!, { recursive: true });

      const argv = bcnArgv(spec.command, spec.args, spec.target, root);
      note(`$ bcn ${[spec.command, spec.target, ...argv.slice(2)].join(' ')}`);
      if (!cancel && !lost && !this.stopping) {
        const inv = invokeBcn(this.o.bcn, argv, {
          cwd: root, env, onLine: line,
          onNoEnvelope: () => note('bcn printed no envelope', 'error'),
        });
        current = inv.proc;
        this.procs.add(inv.proc);
        try { [code, envelope] = await inv.done; } finally { this.procs.delete(inv.proc); current = null; }
      }

      if (!cancel && !lost && !this.stopping) {
        // Collected even when bcn failed: its step file says why, and the server shows it.
        const output = join(dir, 'out.tar.gz');
        const [collected] = await invokeBcn(this.o.bcn, ['bundle', join(root, spec.target), 'collect', '--archive', output, '--quiet'],
          { cwd: root, env, onLine: line }).done;
        if (collected === 0) {
          const put = await fetch(spec.output_url, {
            method: 'PUT', body: readFileSync(output),
            headers: { 'content-type': 'application/gzip', 'content-length': String(statSync(output).size) },
          });
          if (!put.ok) throw new Error(`could not upload the results (${put.status})`);
          outputs = true;
        } else {
          note('the results could not be collected', 'error');
        }
      }
    } catch (e) {
      note(`runner ${this.o.name}: ${(e as Error).message}`, 'error');
      code ??= -1;
    } finally {
      clearInterval(timer);
      await flush();
    }

    if (!lost) {
      try {
        const { status, json } = await this.api(`/api/runner/jobs/${spec.job_id}/complete`, {
          exit_code: code, envelope, log: log.slice(-LOG_LINES), outputs, cancelled: cancel, interrupted: this.stopping,
        }, 15 * 60_000);
        this.log(`job ${spec.job_id}: ${status === 200 ? json.state : `not accepted (${status}): ${json.error ?? ''}`}`);
      } catch (e) {
        this.log(`job ${spec.job_id}: could not report the result: ${(e as Error).message}`);
      }
    } else {
      this.log(`job ${spec.job_id}: the server took the job back; result discarded`);
    }
    rmSync(dir, { recursive: true, force: true });
  }

  private exec(file: string, args: string[]): Promise<void> {
    return new Promise((resolve, reject) => {
      const p = spawn(file, args, { stdio: ['ignore', 'ignore', 'pipe'] });
      let err = '';
      p.stderr.on('data', (b: Buffer) => { err += b; });
      p.on('error', reject);
      p.on('close', (c) => (c === 0 ? resolve() : reject(new Error(`${file} failed: ${err.trim().slice(-300)}`))));
    });
  }
}

async function main(): Promise<number> {
  let runner: Runner;
  try {
    const o = parseOptions(process.argv.slice(2), process.env);
    runner = new Runner(o);
    await runner.authenticate(process.env);
  } catch (e) {
    process.stderr.write(`beacon-runner: ${(e as Error).message}\n`);
    return 2;
  }
  const stop = () => runner.stop();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  await runner.start();
  return 0;
}

main().then((code) => process.exit(code));
