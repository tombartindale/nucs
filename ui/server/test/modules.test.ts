// Add module: POST /api/modules runs bcn project --new, which creates the module's folder and
// its Claude Project pack; the new, empty module then shows in status with that pack.
// Needs DATABASE_URL like the contract suite (see harness.ts).
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { JobDetail, JobSummary, StatusEnvelope } from '@beacon/shared';
import { startBackend, type Backend } from './harness.js';

let b: Backend;

async function json<T>(url: string, method = 'GET', body?: unknown): Promise<{ status: number; body: T }> {
  const r = await fetch(b.url + url, {
    method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
  });
  return { status: r.status, body: await r.json() as T };
}

async function finished(job: JobSummary): Promise<JobDetail> {
  for (let i = 0; i < 200; i++) {
    const d = (await json<JobDetail>(`/api/jobs/${job.id}`)).body;
    if (!['queued', 'running'].includes(d.state)) return d;
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`job ${job.id} did not finish`);
}

beforeAll(async () => { b = await startBackend({ worker: true }); }, 180_000);
afterAll(async () => { await b?.stop(); });

describe('add module', () => {
  it('refuses a malformed code without starting a job', async () => {
    const r = await json<{ error: string }>('/api/modules', 'POST', { code: 'KV70' });
    expect(r.status).toBe(400);
  });

  it('creates the module and its Claude Project pack, which status then lists', async () => {
    const r = await json<JobSummary>('/api/modules', 'POST', { code: 'kv7023', title: 'Data Things', units: 8 });
    expect(r.status).toBe(202);
    expect(r.body.label).toBe('add module KV7023');
    // Just the new module, so it never waits behind jobs on other modules.
    expect(r.body.targets).toEqual(['KV7023']);
    const done = await finished(r.body);
    expect(done.state).toBe('done');
    expect(existsSync(join(b.root, 'KV7023', 'build', 'claude-project', 'instructions.md'))).toBe(true);
    expect(existsSync(join(b.root, 'KV7023', 'course-map.md'))).toBe(false);

    await json('/api/status/refresh', 'POST', {});
    const status = (await json<StatusEnvelope>('/api/status')).body;
    const m = status.summary.modules.KV7023;
    expect(m.topics).toBe(0);
    expect(m.claude_project).toMatchObject({ exists: true, stale: false, units: 8, title: 'Data Things' });

    const instructions = await (await fetch(`${b.url}/files/KV7023/build/claude-project/instructions.md`)).text();
    expect(instructions).toMatch(/^You are working with the leader of KV7023 \(Data Things\)/);
  });

  it('will not create a module that already exists', async () => {
    const r = await json<JobSummary>('/api/modules', 'POST', { code: 'KV7015' });
    const done = await finished(r.body);
    expect(done.state).not.toBe('done');
    expect(done.envelopes.flatMap((e) => e.diagnostics).map((d) => d.code)).toContain('MODULE_EXISTS');
  });

  it('only creates modules through /api/modules: a plain job cannot pass `new`', async () => {
    const r = await json<JobSummary>('/api/jobs', 'POST', { command: 'project', targets: ['.'], args: { new: 'KV7024' } });
    expect(r.status).toBe(202);
    expect(r.body.args).not.toHaveProperty('new');
    await finished(r.body);
    expect(existsSync(join(b.root, 'KV7024'))).toBe(false);
  });
});
