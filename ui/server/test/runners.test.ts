// Routing jobs to remote runners: which pool a job lands in, who may claim it, and what
// happens when a runner vanishes. The HTTP hand-off itself (snapshot, presigned URLs, apply)
// is exercised end to end against a real bucket; see deploy/RUNNER.md.
//
// TODO(postgres): needs DATABASE_URL, like jobs.test.ts.
import { cpSync, mkdtempSync, realpathSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { App, runnerTakes } from '../src/app.js';
import { claimNext, reapExpiredLeases } from '../src/dispatch.js';
import { now } from '../src/util.js';
import { REPO } from './harness.js';

const FAKE = [process.execPath, join(import.meta.dirname, 'fake-bcn.mjs')];
let base: string;
let app: App;

async function addRunner(id: string, opts: { labels?: string[]; commands?: string[]; seen?: string | null } = {}) {
  await app.db.pool.query(
    'INSERT INTO runners(id, name, token_hash, labels, commands, created, last_seen) VALUES($1, $1, $2, $3, $4, $5, $6)',
    [id, `hash-${id}`, JSON.stringify(opts.labels ?? []), JSON.stringify(opts.commands ?? []), now(),
      opts.seen === undefined ? now() : opts.seen]);
}

beforeEach(async () => {
  base = realpathSync(mkdtempSync(join(tmpdir(), 'beacon-runners-')));
  cpSync(join(REPO, 'example'), join(base, 'root'), { recursive: true });
  app = await App.create({ root: join(base, 'root'), bcn: FAKE, dataDir: join(base, 'data') });
  await app.db.pool.query('DELETE FROM jobs');
  await app.db.pool.query('DELETE FROM runners');
  await app.db.pool.query('DELETE FROM prefs');  // the database is shared with the other test files
  app.remoteCommands = ['bumpers', 'compose', 'render'];
});

afterEach(async () => {
  await app.stop();
  rmSync(base, { recursive: true, force: true });
});

describe('runnerTakes', () => {
  it('needs the command listed (or none listed) and every required label', () => {
    const r = { commands: '["compose"]', labels: '["heavy","gpu"]' };
    expect(runnerTakes(r, 'compose', [])).toBe(true);
    expect(runnerTakes(r, 'compose', ['heavy'])).toBe(true);
    expect(runnerTakes(r, 'compose', ['heavy', 'arm'])).toBe(false);
    expect(runnerTakes(r, 'bumpers', [])).toBe(false);
    expect(runnerTakes({ commands: '[]', labels: '[]' }, 'bumpers', [])).toBe(true);
  });
});

describe('routing', () => {
  it('auto runs locally with no runner online, remotely (one job per topic) once one is', async () => {
    const topics = app.topicsUnder('KV7015/U01');
    expect(topics.length).toBeGreaterThan(1);
    let job = await app.submitJob('compose', ['KV7015/U01'], {}, 't');
    expect(job).toMatchObject({ pool: 'local', targets: ['KV7015/U01'] });

    await addRunner('stale', { seen: '2000-01-01T00:00:00Z' });
    expect((await app.submitJob('compose', ['KV7015/U01'], {}, 't')).pool).toBe('local');

    await addRunner('studio');
    job = await app.submitJob('compose', ['KV7015/U01'], {}, 't');
    expect(job).toMatchObject({ pool: 'remote', targets: [topics[0]] });
    const remote = (await app.jobs.list()).filter((j) => j.pool === 'remote');
    expect(remote.map((j) => j.targets[0]).sort()).toEqual(topics);
  });

  it('only routes commands bcn can carry, and never one set to local', async () => {
    await addRunner('studio');
    await app.db.setPrefs({ routing: { compose: 'local', validate: 'remote' } });
    expect((await app.submitJob('compose', ['KV7015/U01/T01'], {}, 't')).pool).toBe('local');
    expect((await app.submitJob('validate', ['KV7015/U01/T01'], {}, 't')).pool).toBe('local');
  });

  it('remote waits for a runner; required labels must match one for auto', async () => {
    await app.db.setPrefs({ routing: { compose: 'remote', bumpers: 'auto' }, routing_labels: { bumpers: ['heavy'] } });
    expect((await app.submitJob('compose', ['KV7015/U01/T01'], {}, 't')).pool).toBe('remote');
    await addRunner('light');
    expect((await app.submitJob('bumpers', ['KV7015/U01/T01'], {}, 't')).pool).toBe('local');
    await addRunner('big', { labels: ['heavy'] });
    expect((await app.submitJob('bumpers', ['KV7015/U01/T01'], {}, 't')).pool).toBe('remote');
  });
});

describe('claiming', () => {
  const remote = (id: string, accept?: Parameters<typeof claimNext>[1]['accept']) =>
    claimNext(app.db, { pool: 'remote', id, runnerName: id, leaseMs: 30_000, accept });

  it('a pool only claims its own jobs, and a runner only those it takes', async () => {
    const placement = { pool: 'remote' as const, requires: ['heavy'] };
    const r = await app.jobs.submit('compose', ['KV7015/U01/T01'], {}, 'c', 't', placement);
    expect(await claimNext(app.db, { pool: 'local', id: 'worker' })).toBeUndefined();
    expect(await remote('light', (row) => runnerTakes({ commands: '[]', labels: '[]' }, row.command, JSON.parse(row.requires))))
      .toBeUndefined();
    const claimed = await remote('big', (row) => runnerTakes({ commands: '[]', labels: '["heavy"]' }, row.command, JSON.parse(row.requires)));
    expect(claimed).toMatchObject({ id: r.id, state: 'running', runner_id: 'big' });
    expect((await app.jobs.get(r.id))!.runner).toBe('big');
  });

  it('never runs a remote and a local job on the same topic, even claimed at once', async () => {
    const a = await app.jobs.submit('compose', ['KV7015/U01/T01'], {}, 'a', 't', { pool: 'remote', requires: [] });
    const b = await app.jobs.submit('validate', ['KV7015/U01'], {}, 'b', 't');
    const [x, y] = await Promise.all([remote('r1'), claimNext(app.db, { pool: 'local', id: 'w1' })]);
    expect([x?.id, y?.id].filter(Boolean)).toHaveLength(1);
    expect([a.id, b.id]).toContain((x ?? y)!.id);
  });

  it('a lapsed lease interrupts a job that started, and re-queues one that never reported', async () => {
    const started = await app.jobs.submit('compose', ['KV7015/U01/T01'], {}, 'a', 't', { pool: 'remote', requires: [] });
    const silent = await app.jobs.submit('compose', ['KV7015/U01/T02'], {}, 'b', 't', { pool: 'remote', requires: [] });
    await remote('r1');
    await remote('r2');
    const past = '2000-01-01T00:00:00Z';
    await app.db.pool.query('UPDATE jobs SET lease_expires_at=$1', [past]);
    await app.db.pool.query('UPDATE jobs SET last_event=$1 WHERE id=$2', [now(), started.id]);
    await reapExpiredLeases(app.db, app.bus);
    expect((await app.jobs.get(started.id))!.state).toBe('interrupted');
    expect(await app.jobs.get(silent.id)).toMatchObject({ state: 'queued', runner: null, started: null });
  });
});
