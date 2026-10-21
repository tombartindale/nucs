// Taking a queued job and putting a finished one back, shared by the two kinds of claimer:
// a local worker process (scheduler.ts, pool 'local') and a remote runner reached over
// HTTPS (runner-api.ts, pool 'remote'). Which pool a job is in is decided when it is
// submitted (see App.route in app.ts).
//
// Claims are serialised with a transaction-scoped advisory lock: the overlap check reads
// every running row and then locks a queued one, and without the lock two claimers doing
// that at the same moment could each see "no overlap" and start jobs on the same topic.
import type { AnyEnvelope } from '@beacon/shared';
import type { EventBus } from './bus.js';
import type { DB, JobRow } from './db.js';
import { overlaps, summaryFromRow } from './jobs.js';
import { now } from './util.js';

export const LOG_LINES = 400;
const CLAIM_LOCK = 0x62636e01;

export type Pool = 'local' | 'remote';

export interface Claimer {
  pool: Pool;
  /** A local worker's id, or a runner's id. */
  id: string;
  /** Remote only: shown on the job, and how long the runner has before it must report in. */
  runnerName?: string;
  leaseMs?: number;
  /** Remote only: whether this runner will take the job (its commands and labels). */
  accept?: (row: JobRow) => boolean;
}

export async function claimNext(db: DB, claimer: Claimer): Promise<JobRow | undefined> {
  const client = await db.pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock($1)', [CLAIM_LOCK]);
    // Every currently-running row (from any worker or runner) counts toward the overlap
    // check, so two claimers never touch the same topic even though neither can see the
    // other's in-memory state.
    const busyRes = await client.query<{ targets: string }>("SELECT targets FROM jobs WHERE state='running'");
    const busyTargets = busyRes.rows.flatMap((r) => JSON.parse(r.targets) as string[]);
    const queuedRes = await client.query<JobRow>(
      "SELECT * FROM jobs WHERE state='queued' AND pool=$1 ORDER BY id FOR UPDATE SKIP LOCKED", [claimer.pool]);
    const row = queuedRes.rows.find((r) => {
      const targets = JSON.parse(r.targets) as string[];
      return !targets.some((a) => busyTargets.some((b) => overlaps(a, b))) && (claimer.accept?.(r) ?? true);
    });
    let claimed: JobRow | undefined;
    if (row) {
      const res = claimer.pool === 'local'
        ? await client.query<JobRow>("UPDATE jobs SET state='running', started=$1, worker_id=$2 WHERE id=$3 RETURNING *",
          [now(), claimer.id, row.id])
        : await client.query<JobRow>(
          "UPDATE jobs SET state='running', started=$1, runner_id=$2, runner_name=$3, lease_expires_at=$4 WHERE id=$5 RETURNING *",
          [now(), claimer.id, claimer.runnerName ?? '', leaseUntil(claimer.leaseMs ?? 30_000), row.id]);
      claimed = res.rows[0];
    }
    await client.query('COMMIT');
    return claimed;
  } catch (e) {
    await client.query('ROLLBACK');
    throw e;
  } finally {
    client.release();
  }
}

export function leaseUntil(ms: number): string {
  return now(new Date(Date.now() + ms));
}

export interface Finish {
  state: 'done' | 'failed' | 'cancelled' | 'interrupted';
  exitCode: number | null;
  durationMs: number;
  envelopes: AnyEnvelope[];
  log: string[];
}

export async function finishJob(db: DB, bus: EventBus, id: number, f: Finish): Promise<void> {
  const log = f.log.length > LOG_LINES ? f.log.slice(f.log.length - LOG_LINES) : f.log;
  const res = await db.pool.query<JobRow>(
    `UPDATE jobs SET finished=$1, state=$2, exit_code=$3, duration_ms=$4, envelope=$5, log=$6, worker_id=NULL, lease_expires_at=NULL
     WHERE id=$7 RETURNING *`,
    [now(), f.state, f.exitCode, f.durationMs, f.envelopes.length ? JSON.stringify(f.envelopes) : null, log.join('\n'), id]);
  if (res.rows[0]) bus.publish({ type: 'job', job: summaryFromRow(res.rows[0]) });
}

/** Local jobs left 'running' by a worker that stopped heartbeating (crashed, killed, OOM). */
export async function reapAbandonedLocal(db: DB, deadBefore: string): Promise<void> {
  await db.pool.query(
    `UPDATE jobs SET state='interrupted', finished=$1, worker_id=NULL
     WHERE state='running' AND pool='local' AND (
       worker_id IS NULL
       OR worker_id NOT IN (SELECT worker_id FROM worker_heartbeats)
       OR worker_id IN (SELECT worker_id FROM worker_heartbeats WHERE last_seen < $2)
     )`,
    [now(), deadBefore]);
}

/** Remote jobs whose runner stopped reporting in before its lease ran out (killed, offline).
 *  One that never reported at all (the runner hung up before it received the job) goes back
 *  in the queue for another runner; one that had started is interrupted, as a local job is. */
export async function reapExpiredLeases(db: DB, bus: EventBus): Promise<void> {
  const res = await db.pool.query<JobRow>(
    `UPDATE jobs SET
       state = CASE WHEN last_event IS NULL THEN 'queued' ELSE 'interrupted' END,
       finished = CASE WHEN last_event IS NULL THEN NULL ELSE $1 END,
       started = CASE WHEN last_event IS NULL THEN NULL ELSE started END,
       runner_id = CASE WHEN last_event IS NULL THEN NULL ELSE runner_id END,
       runner_name = CASE WHEN last_event IS NULL THEN NULL ELSE runner_name END,
       lease_expires_at = NULL
     WHERE state='running' AND pool='remote' AND lease_expires_at < $1 RETURNING *`, [now()]);
  for (const row of res.rows) bus.publish({ type: 'job', job: summaryFromRow(row) });
  if (res.rows.some((r) => r.state === 'queued')) bus.publish({ type: 'job-wake' });
}
