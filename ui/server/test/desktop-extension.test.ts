// The Claude Desktop extension end to end, run the way Claude Desktop runs it: as a local
// process spoken to over stdio, configured by environment and arguments, forwarding to a
// real backend's /mcp and uploading files from disk for import_from_disk.
// Needs DATABASE_URL like the contract suite (see harness.ts).
import { mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { ApiTokenCreated } from '@beacon/shared';
import { REPO, startBackend, type Backend } from './harness.js';

const ENTRY = join(REPO, 'ui', 'desktop-extension', 'src', 'index.ts');
let b: Backend;
let token: string;
let allowed: string;
let outside: string;
const clients: Client[] = [];

async function extension(tok: string, folders: string[]): Promise<Client> {
  const c = new Client({ name: 'claude-desktop-test', version: '0' });
  await c.connect(new StdioClientTransport({
    command: process.execPath, args: ['--import', 'tsx', ENTRY, ...folders],
    env: { ...process.env, NUCS_URL: b.url, NUCS_TOKEN: tok } as Record<string, string>,
    stderr: 'pipe',
  }));
  clients.push(c);
  return c;
}

const textOf = (r: unknown) => ((r as { content: unknown }).content as Array<{ type: string; text: string }>).map((c) => c.text).join('\n');

beforeAll(async () => {
  b = await startBackend({ worker: true });
  const r = await fetch(`${b.url}/api/tokens`, { method: 'POST', body: JSON.stringify({ name: 'extension test' }), headers: { 'Content-Type': 'application/json' } });
  token = (await r.json() as ApiTokenCreated).token;
  const base = realpathSync(mkdtempSync(join(tmpdir(), 'nucs-ext-')));
  allowed = join(base, 'inbox');
  outside = join(base, 'elsewhere');
  mkdirSync(join(allowed, 'KV7015-U01'), { recursive: true });
  mkdirSync(outside, { recursive: true });
  const topic = readFileSync(join(b.root, 'KV7015/U01/T01/topic.md'), 'utf8');
  // A developer's re-send, flat-named the way the assistant names files.
  writeFileSync(join(allowed, 'KV7015-U01', 'KV7015-U01-T01.md'), topic.replace('Topics are broad', 'Topics are rather broad'));
  writeFileSync(join(outside, 'KV7015-U01-T01.md'), topic);
}, 180_000);
afterAll(async () => {
  for (const c of clients) await c.close().catch(() => {});
  await b?.stop();
});

describe('Claude Desktop extension', () => {
  it("lists the server's tools and prompts plus import_from_disk", async () => {
    const c = await extension(token, [allowed]);
    const tools = (await c.listTools()).tools.map((t) => t.name);
    expect(tools).toEqual(expect.arrayContaining(['programme_overview', 'add_qa_comment', 'intake_check', 'import_from_disk']));
    expect((await c.listPrompts()).prompts.map((p) => p.name)).toContain('import_tutor_content');
    const overview = await c.callTool({ name: 'programme_overview', arguments: {} });
    expect(textOf(overview)).toContain('KV7015');
  });

  it('explains a bad token instead of failing obscurely', async () => {
    const c = await extension('nucs_wrong', [allowed]);
    await expect(c.listTools()).rejects.toThrow(/did not accept the API token/);
  });

  it('only reads inside the allowed folders', async () => {
    const c = await extension(token, [allowed]);
    const r = await c.callTool({ name: 'import_from_disk', arguments: { path: join(outside, 'KV7015-U01-T01.md') } });
    expect(r.isError).toBe(true);
    expect(textOf(r)).toMatch(/outside the folders NUCS may read/);
  });

  it('checks a folder from disk without writing, then refuses to overwrite without replace', async () => {
    const c = await extension(token, [allowed]);
    const stored = join(b.root, 'KV7015/U01/T01/topic.md');
    const before = readFileSync(stored, 'utf8');

    const checked = await c.callTool({ name: 'import_from_disk', arguments: { path: join(allowed, 'KV7015-U01') } });
    expect(checked.isError).toBeFalsy();
    const t = textOf(checked);
    expect(t).toContain('Checked (nothing written)');
    expect(t).toContain('exists_differs');
    expect(t).toContain('rather broad');
    expect(readFileSync(stored, 'utf8')).toBe(before);

    const imported = await c.callTool({ name: 'import_from_disk', arguments: { path: join(allowed, 'KV7015-U01'), check_only: false } });
    expect(textOf(imported)).toContain('exists_differs');
    expect(readFileSync(stored, 'utf8')).toBe(before);

    const jobs = await (await fetch(`${b.url}/api/jobs?limit=5`)).json() as { jobs: Array<{ label: string; by: string }> };
    expect(jobs.jobs.find((j) => j.label.includes('via Claude'))?.by).toBe('test@localhost');
  });

  it('replaces only when asked', async () => {
    const c = await extension(token, [allowed]);
    const r = await c.callTool({ name: 'import_from_disk', arguments: { path: join(allowed, 'KV7015-U01'), check_only: false, replace: true } });
    expect(r.isError).toBeFalsy();
    expect(readFileSync(join(b.root, 'KV7015/U01/T01/topic.md'), 'utf8')).toContain('rather broad');
  });
});

afterAll(() => { try { rmSync(join(allowed, '..'), { recursive: true, force: true }); } catch { /* fine */ } });
