// The MCP endpoint end to end, the way the Claude Desktop extension uses it: a real backend
// over a copy of example/, a personal API token, and the MCP SDK's own client.
// Needs DATABASE_URL like the contract suite (see harness.ts).
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { ApiTokenCreated, ApiTokensResponse } from '@beacon/shared';
import { startBackend, type Backend } from './harness.js';

const ACTIVITY = 'activity:KV7015/U01';
let b: Backend;
let token: ApiTokenCreated;
let client: Client;

async function json<T>(url: string, method = 'GET', body?: unknown, headers: Record<string, string> = {}): Promise<{ status: number; body: T }> {
  const r = await fetch(b.url + url, {
    method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
  });
  const text = await r.text();
  let data: unknown = text;
  try { data = JSON.parse(text); } catch { /* not JSON */ }
  return { status: r.status, body: data as T };
}

async function connect(tok: string): Promise<Client> {
  const c = new Client({ name: 'test', version: '0' });
  await c.connect(new StreamableHTTPClientTransport(new URL(`${b.url}/mcp`), { requestInit: { headers: { Authorization: `Bearer ${tok}` } } }));
  return c;
}

/** A tool's text result, parsed as JSON when it is JSON. */
async function callTool<T = Record<string, unknown>>(name: string, args: Record<string, unknown> = {}): Promise<{ isError: boolean; value: T }> {
  const r = await client.callTool({ name, arguments: args });
  const t = (r.content as Array<{ type: string; text: string }>)[0].text;
  let value: unknown = t;
  try { value = JSON.parse(t); } catch { /* plain text */ }
  return { isError: !!r.isError, value: value as T };
}

beforeAll(async () => {
  b = await startBackend({ worker: true });
  mkdirSync(join(b.root, 'KV7015/U01/T01/build'), { recursive: true });
  writeFileSync(join(b.root, 'KV7015/U01/T01/build/composed.zh.mp4'), 'not really a video');
  copyFileSync(join(b.root, 'KV7015/U01/activity.md'), join(b.root, 'KV7015/U01/activity.zh.md'));
  await json('/api/status/refresh', 'POST', {});
  token = (await json<ApiTokenCreated>('/api/tokens', 'POST', { name: 'mcp test' })).body;
  client = await connect(token.token);
}, 180_000);
afterAll(async () => {
  await client?.close();
  await b?.stop();
});

describe('MCP auth', () => {
  it('needs a token, and refuses a made-up one', async () => {
    expect((await json('/mcp', 'POST', {})).status).toBe(401);
    expect((await json('/mcp', 'POST', {}, { Authorization: 'Bearer nucs_nope' })).status).toBe(401);
  });

  it('refuses a forged internal header', async () => {
    const r = await json('/api/qa', 'GET', undefined, { 'x-nucs-internal': 'guess', 'x-nucs-as': 'someone@else.org' });
    expect(r.status).toBe(403);
  });

  it('is stateless: GET is not allowed', async () => {
    expect((await json('/mcp', 'GET', undefined, { Authorization: `Bearer ${token.token}` })).status).toBe(405);
  });

  it('lists the token, and a revoked token stops working', async () => {
    const extra = (await json<ApiTokenCreated>('/api/tokens', 'POST', { name: 'to revoke' })).body;
    expect(extra.token).toMatch(/^nucs_/);
    const listed = (await json<ApiTokensResponse>('/api/tokens')).body.tokens.map((t) => t.id);
    expect(listed).toContain(extra.id);
    const c = await connect(extra.token);
    expect((await c.listTools()).tools.length).toBeGreaterThan(0);
    await c.close();
    expect((await json(`/api/tokens/${extra.id}`, 'DELETE')).status).toBe(200);
    await expect(connect(extra.token)).rejects.toThrow();
    expect((await json<ApiTokensResponse>('/api/tokens')).body.tokens.map((t) => t.id)).not.toContain(extra.id);
  });
});

describe('MCP tools', () => {
  it('lists the tools and prompts', async () => {
    const tools = (await client.listTools()).tools.map((t) => t.name);
    expect(tools).toEqual(expect.arrayContaining([
      'programme_overview', 'get_topic', 'get_topic_source', 'read_document', 'get_subtitles', 'get_diagnostics',
      'list_qa_items', 'get_qa_item', 'add_qa_comment', 'resolve_qa_comment', 'set_qa_status',
      'intake_check', 'intake_submit', 'get_job_result',
    ]));
    const prompts = (await client.listPrompts()).prompts.map((p) => p.name);
    expect(prompts).toEqual(expect.arrayContaining(['review_qa_item', 'reconcile_module', 'import_tutor_content']));
  });

  it('reads the programme, a topic and a document as stored', async () => {
    const all = await callTool<{ modules: Array<{ module: string }> }>('programme_overview');
    expect(all.value.modules.map((m) => m.module)).toContain('KV7015');
    const mod = await callTool<{ topic_list: Array<{ topic: string }> }>('programme_overview', { module: 'KV7015' });
    expect(mod.value.topic_list.map((t) => t.topic)).toContain('KV7015-U01-T01');

    const src = await callTool<string>('get_topic_source', { topic: 'KV7015-U01-T01' });
    expect(src.value).toBe(readFileSync(join(b.root, 'KV7015/U01/T01/topic.md'), 'utf8').replace(/^﻿/, ''));
    const topic = await callTool<{ script: { slides: Array<{ narration: string }> } }>('get_topic', { topic: 'KV7015-U01-T01' });
    expect(topic.value.script.slides.length).toBeGreaterThan(0);

    const map = await callTool<string>('read_document', { path: 'KV7015/course-map.md' });
    expect(map.value).toContain('KV7015');
    const missing = await callTool('read_document', { path: 'KV7015/assignment-9.md' });
    expect(missing.isError).toBe(true);
  });

  it('reads and writes QA as the token owner, marked via Claude', async () => {
    const items = await callTool<Array<{ item: string }>>('list_qa_items', { module: 'KV7015' });
    expect(items.value.map((i) => i.item)).toContain(ACTIVITY);

    const added = await callTool<{ added: { id: number; by: string; via: string; anchorLine: number } }>('add_qa_comment',
      { item: ACTIVITY, body: 'Heading left in English', kind: 'suggestion', anchor_line: 2 });
    expect(added.isError).toBe(false);
    expect(added.value.added).toMatchObject({ by: 'test@localhost', via: 'claude', anchorLine: 2 });

    // The app sees the same comment, with where it came from.
    const viaApp = await json<{ comments: Array<{ id: number; via: string | null }> }>(`/api/qa/item/${encodeURIComponent(ACTIVITY)}`);
    expect(viaApp.body.comments.find((c) => c.id === added.value.added.id)?.via).toBe('claude');

    const resolved = await callTool<{ openComments: number }>('resolve_qa_comment', { comment_id: added.value.added.id });
    expect(resolved.isError).toBe(false);
    const status = await callTool<{ status: string }>('set_qa_status', { item: ACTIVITY, status: 'changes_needed' });
    expect(status.value.status).toBe('changes_needed');
    await json(`/api/qa/comments/${added.value.added.id}`, 'DELETE');
    await json(`/api/qa/item/${encodeURIComponent(ACTIVITY)}/status`, 'PUT', { status: 'to_review' });

    const bad = await callTool('add_qa_comment', { item: 'video:KV7015-U01-T02', body: 'x' });
    expect(bad.isError).toBe(true);
  });

  it('checks intake without writing, and never overwrites a differing topic without replace', async () => {
    const path = join(b.root, 'KV7015/U01/T01/topic.md');
    const original = readFileSync(path, 'utf8');

    const same = await callTool<{ state: string; envelopes: Array<{ results: Array<{ topic: string; action: string }> }> }>(
      'intake_check', { text: original });
    expect(same.value.state).toBe('done');
    expect(same.value.envelopes[0].results.find((r) => r.topic === 'KV7015-U01-T01')?.action).toBe('unchanged');

    const changed = original.replace('Topics are broad', 'Topics are very broad');
    const checked = await callTool<{ envelopes: Array<{ results: Array<{ topic: string; action: string }> }> }>('intake_check', { text: changed });
    expect(checked.value.envelopes[0].results.find((r) => r.topic === 'KV7015-U01-T01')?.action).toBe('exists_differs');
    expect(readFileSync(path, 'utf8')).toBe(original);

    const submitted = await callTool<{ label: string; envelopes: Array<{ results: Array<{ topic: string; action: string; diff?: string }> }> }>(
      'intake_submit', { text: changed });
    const r = submitted.value.envelopes[0].results.find((x) => x.topic === 'KV7015-U01-T01');
    expect(r?.action).toBe('exists_differs');
    expect(r?.diff).toContain('very broad');
    expect(submitted.value.label).toContain('via Claude');
    expect(readFileSync(path, 'utf8')).toBe(original);
  }, 120_000);
});
