// The Mandarin QA routes end to end: a real backend over a copy of example/, given a
// composed Mandarin video and a Mandarin activity, then a verdict and comment round trip.
// Needs DATABASE_URL like the contract suite (see harness.ts).
import { copyFileSync, mkdirSync, utimesSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { QaItemResponse, QaListResponse } from '@beacon/shared';
import { startBackend, type Backend } from './harness.js';

const VIDEO = 'video:KV7015-U01-T01';
const ACTIVITY = 'activity:KV7015/U01';

let b: Backend;
beforeAll(async () => {
  b = await startBackend();
  mkdirSync(join(b.root, 'KV7015/U01/T01/build'), { recursive: true });
  writeFileSync(join(b.root, 'KV7015/U01/T01/build/composed.zh.mp4'), 'not really a video');
  copyFileSync(join(b.root, 'KV7015/U01/activity.md'), join(b.root, 'KV7015/U01/activity.zh.md'));
  await fetch(`${b.url}/api/status/refresh`, { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' } });
  // The database is shared between runs: start each item from a clean slate.
  for (const item of [VIDEO, ACTIVITY]) {
    const r = await get<QaItemResponse>(item);
    for (const c of r.comments) await send(`/api/qa/comments/${c.id}`, 'DELETE');
    await send(`${path(item)}/status`, 'PUT', { status: 'to_review' });
  }
}, 180_000);
afterAll(async () => { await b?.stop(); });

const path = (item: string) => `/api/qa/item/${encodeURIComponent(item)}`;
async function send<T = QaItemResponse>(url: string, method: string, body?: unknown): Promise<{ status: number; body: T }> {
  const r = await fetch(b.url + url, { method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' } });
  return { status: r.status, body: await r.json() as T };
}
async function get<T>(item: string): Promise<T> { return (await send<T>(path(item), 'GET')).body; }

describe('QA API', () => {
  it('lists the composed Mandarin video and the Mandarin activity', async () => {
    const { body } = await send<QaListResponse>('/api/qa', 'GET');
    const keys = body.items.map((i) => i.item);
    expect(keys).toContain(VIDEO);
    expect(keys).toContain(ACTIVITY);
    expect(keys).not.toContain('video:KV7015-U01-T02');
    const act = body.items.find((i) => i.item === ACTIVITY)!;
    expect(act).toMatchObject({ type: 'activity', path: 'KV7015/U01/activity.zh.md', status: 'to_review' });
  });

  it('takes anchored comments, resolves and deletes them', async () => {
    const added = await send(`${path(ACTIVITY)}/comments`, 'POST', { body: 'Heading not translated', kind: 'suggestion', anchorLine: 3 });
    expect(added.status).toBe(200);
    expect(added.body.comments).toHaveLength(1);
    const c = added.body.comments[0];
    expect(c).toMatchObject({ body: 'Heading not translated', kind: 'suggestion', anchorLine: 3, anchorT: null, resolved: false });

    const resolved = await send(`/api/qa/comments/${c.id}`, 'PATCH', { resolved: true });
    expect(resolved.body.comments[0].resolved).toBe(true);
    expect(resolved.body.item).toMatchObject({ comments: 1, openComments: 0 });

    const deleted = await send(`/api/qa/comments/${c.id}`, 'DELETE');
    expect(deleted.body.comments).toEqual([]);
  });

  it('records a verdict and flags the file changing after it', async () => {
    const approved = await send(`${path(VIDEO)}/status`, 'PUT', { status: 'approved' });
    expect(approved.body.item).toMatchObject({ status: 'approved', changedSinceReview: false });
    expect(approved.body.item.statusAt).toBeTruthy();

    const later = new Date(Date.now() + 60_000);
    utimesSync(join(b.root, 'KV7015/U01/activity.zh.md'), later, later);
    await send(`${path(ACTIVITY)}/status`, 'PUT', { status: 'changes_needed' });
    const future = new Date(Date.now() + 120_000);
    utimesSync(join(b.root, 'KV7015/U01/activity.zh.md'), future, future);
    expect((await get<QaItemResponse>(ACTIVITY)).item.changedSinceReview).toBe(true);
  });

  it('refuses bad input', async () => {
    expect((await send(`${path(VIDEO)}/status`, 'PUT', { status: 'done' })).status).toBe(400);
    expect((await send(`${path(VIDEO)}/comments`, 'POST', { body: '   ' })).status).toBe(400);
    expect((await send(`${path(VIDEO)}/comments`, 'POST', { body: 'x', anchorT: -1 })).status).toBe(400);
    expect((await send(path('video:KV7015-U01-T02'), 'GET')).status).toBe(400);  // no Mandarin video yet
    expect((await send(path('activity:../../etc'), 'GET')).status).toBe(400);
    expect((await send('/api/qa/comments/999999', 'PATCH', { resolved: true })).status).toBe(404);
  });
});
