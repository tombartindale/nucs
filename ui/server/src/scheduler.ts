// The worker's half of the job queue: dequeues 'queued' rows from Postgres, runs bcn for
// each target, and writes progress/results back to the same table. Any number of workers
// (even on different hosts) can run this against the same DATABASE_URL/REDIS_URL safely:
// dequeuing uses `SELECT ... FOR UPDATE SKIP LOCKED` so two workers never pick up the same
// job, and the overlap check considers every currently-running row, not just this worker's
// own, so two workers never run overlapping targets either. A worker only takes jobs in
// the 'local' pool; jobs routed to a remote runner are claimed over HTTPS instead (see
// runner-api.ts), from the same table and under the same overlap rule (dispatch.ts).
//
// This is a direct port of the single-process JobQueue.run()/invoke()/schedule() logic
// that used to live in jobs.ts, before the API and the job runner became separate
// processes/containers.
import type { ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import type { AnyEnvelope, JobArgs, Prefs } from '@beacon/shared';
import { bcnArgv, invokeBcn, parseEvent } from './bcn-invoke.js';
import type { Bcn } from './bcn.js';
import type { EventBus } from './bus.js';
import type { DB, JobRow } from './db.js';
import { claimNext, finishJob, reapAbandonedLocal, reapExpiredLeases } from './dispatch.js';
import { PIPELINE, summaryFromRow } from './jobs.js';
import { now, pyJson, sleep } from './util.js';

const POLL_MS = 2000;
const HEARTBEAT_MS = 5000;
// A worker missing this many heartbeats in a row is presumed dead; its running jobs are
// reclaimed. Generous relative to HEARTBEAT_MS so a slow GC pause never falsely reaps a
// job that is, in fact, still running fine.
const HEARTBEAT_DEAD_MS = HEARTBEAT_MS * 6;
const REAP_MS = 15_000;

interface RunningJob {
  id: number;
  targets: string[];
  targetIndex: number;
  cancelRequested: boolean;
  proc: ChildProcess | null;
  log: string[];
  envelopes: AnyEnvelope[];
  t0: number;
}

export class Scheduler {
  readonly workerId = randomUUID();
  private running = new Map<number, RunningJob>();
  private wakePending = false;
  private wakeWaiters: Array<() => void> = [];
  private heartbeatTimer: NodeJS.Timeout | null = null;
  private reapTimer: NodeJS.Timeout | null = null;
  private stopped = false;

  constructor(private db: DB, private bus: EventBus, private bcn: Bcn, private root: string,
    private prefs: () => Promise<Prefs>) {}

  start(): void {
    this.bus.subscribe((ev) => { if (ev.type === 'job-wake') this.wake(); });
    void this.heartbeat();
    this.heartbeatTimer = setInterval(() => void this.heartbeat(), HEARTBEAT_MS);
    void this.reapAbandoned();
    this.reapTimer = setInterval(() => void this.reapAbandoned(), REAP_MS);
    void this.loop();
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    if (this.reapTimer) clearInterval(this.reapTimer);
    for (const j of this.running.values()) if (j.proc && j.proc.exitCode === null) j.proc.kill('SIGINT');
    // Let bcn's own SIGINT handling clean up partial output before this process exits.
    const deadline = Date.now() + 10_000;
    while (this.running.size && Date.now() < deadline) await sleep(200);
    await this.db.pool.query('DELETE FROM worker_heartbeats WHERE worker_id=$1', [this.workerId]);
  }

  private async heartbeat(): Promise<void> {
    await this.db.pool.query(
      'INSERT INTO worker_heartbeats(worker_id, last_seen) VALUES($1, $2) ON CONFLICT (worker_id) DO UPDATE SET last_seen=excluded.last_seen',
      [this.workerId, now()]);
  }

  /** Jobs left 'running' by a worker that stopped heartbeating (crashed, killed, OOM) are
   *  marked interrupted, the same way a restarted single-process backend used to recover
   *  its own crashed jobs — except this also covers a *different* worker process dying.
   *  Remote jobs whose runner's lease ran out are reaped here too, so a runner that
   *  vanished is noticed even while no runner is polling. */
  private async reapAbandoned(): Promise<void> {
    try {
      await reapAbandonedLocal(this.db, now(new Date(Date.now() - HEARTBEAT_DEAD_MS)));
      await reapExpiredLeases(this.db, this.bus);
    } catch (e) {
      process.stderr.write(`scheduler: reap failed: ${(e as Error).message}\n`);
    }
  }

  private async loop(): Promise<void> {
    while (!this.stopped) {
      try {
        await this.tick();
      } catch (e) {
        // A single bad tick (e.g. a transient DB hiccup) must not kill the worker process.
        process.stderr.write(`scheduler: tick failed: ${(e as Error).message}\n`);
      }
      await this.sleepOrWake(POLL_MS);
    }
  }

  /** Like sleep(ms), but a `job-wake` arriving during (or just before) the wait resolves it
   *  immediately, so a freshly queued job doesn't sit idle for up to POLL_MS before an idle
   *  worker even looks at it. POLL_MS remains the fallback for a missed wake (e.g. a Redis
   *  hiccup) - a wake is a latency optimisation, not a replacement for the periodic tick. */
  private wake(): void {
    this.wakePending = true;
    const waiters = this.wakeWaiters;
    this.wakeWaiters = [];
    for (const w of waiters) w();
  }

  private sleepOrWake(ms: number): Promise<void> {
    if (this.wakePending) { this.wakePending = false; return Promise.resolve(); }
    return new Promise((resolve) => {
      const onWake = () => { clearTimeout(timer); resolve(); };
      const timer = setTimeout(() => {
        this.wakeWaiters = this.wakeWaiters.filter((w) => w !== onWake);
        resolve();
      }, ms);
      this.wakeWaiters.push(onWake);
    });
  }

  private async tick(): Promise<void> {
    const prefs = await this.prefs();
    const limit = Math.max(1, Math.trunc(Number(prefs.parallel_jobs) || 2));
    if (this.running.size >= limit) return;

    const row = await claimNext(this.db, { pool: 'local', id: this.workerId });

    if (row) {
      this.bus.publish({ type: 'job', job: summaryFromRow(row) });
      void this.run(row);
      // Immediately look for more capacity, in case parallel_jobs > 1 and several jobs
      // with disjoint targets are queued at once.
      void this.tick();
    }
  }

  private async run(row: JobRow): Promise<void> {
    const targets: string[] = JSON.parse(row.targets);
    const args: JobArgs = JSON.parse(row.args);
    const job: RunningJob = { id: row.id, targets, targetIndex: 0, cancelRequested: false, proc: null, log: [], envelopes: [], t0: Date.now() };
    this.running.set(row.id, job);
    const codes: number[] = [];
    try {
      for (let i = 0; i < targets.length; i++) {
        if (await this.cancelRequested(row.id)) { job.cancelRequested = true; break; }
        job.targetIndex = i;
        await this.db.pool.query('UPDATE jobs SET target_index=$1 WHERE id=$2', [i, row.id]);
        const [code, envelope] = await this.invoke(job, row.command, args, targets[i]);
        codes.push(code);
        if (envelope) job.envelopes.push(envelope);
      }
    } catch (e) {
      job.log.push(pyJson({ event: 'log', level: 'error', message: `worker error: ${(e as Error).message}` }));
      codes.push(-1);
    }
    const exitCode = codes.length ? (codes.find((c) => c !== 0) ?? 0) : null;
    const state = job.cancelRequested ? 'cancelled' : (codes.length && codes.every((c) => c === 0) ? 'done' : 'failed');
    await finishJob(this.db, this.bus, row.id, { state, exitCode, durationMs: Date.now() - job.t0, envelopes: job.envelopes, log: job.log });
    this.running.delete(row.id);
    void this.tick();
  }

  private async cancelRequested(id: number): Promise<boolean> {
    const res = await this.db.pool.query<{ cancel_requested: boolean }>('SELECT cancel_requested FROM jobs WHERE id=$1', [id]);
    return res.rows[0]?.cancel_requested ?? false;
  }

  private async invoke(job: RunningJob, command: string, args: JobArgs, target: string): Promise<[number, AnyEnvelope | null]> {
    const argv = bcnArgv(command, args, target, this.root);
    if (PIPELINE.has(command)) {
      const prefs = await this.prefs();
      argv.push('--jobs', String(Math.max(1, Math.trunc(Number(prefs.jobs) || 1))));
    }
    job.log.push(pyJson({ event: 'log', level: 'info', message: `$ bcn ${argv.join(' ')}` }));
    const { proc: p, done } = invokeBcn(this.bcn.prefix, argv, {
      cwd: this.root,
      onLine: (line) => {
        job.log.push(line);
        const ev = parseEvent(line);
        if (ev.event === 'progress') {
          void this.db.pool.query('UPDATE jobs SET progress=$1, last_event=$2 WHERE id=$3', [JSON.stringify(ev), now(), job.id]);
        } else {
          void this.db.pool.query('UPDATE jobs SET last_event=$1 WHERE id=$2', [now(), job.id]);
        }
        this.bus.publish({ type: 'job-event', job: job.id, target_index: job.targetIndex, target_count: job.targets.length, event: ev });
      },
      onNoEnvelope: () => job.log.push(pyJson({ event: 'log', level: 'error', message: 'bcn printed no envelope' })),
    });
    job.proc = p;
    // run()'s own cancelRequested() check only runs between targets, before invoke() is
    // called for the next one — it never re-checks while this process is actually running,
    // so a single-target job (the common case) ignored Cancel entirely until it finished on
    // its own. This polls the same flag while the process is alive and signals it directly.
    const watchCancel = setInterval(() => {
      void this.cancelRequested(job.id).then((requested) => {
        if (!requested || job.proc !== p) return;
        clearInterval(watchCancel);
        job.cancelRequested = true;
        p.kill('SIGINT');
      });
    }, 1000);
    try {
      return await done;
    } finally {
      clearInterval(watchCancel);
      job.proc = null;
    }
  }
}
