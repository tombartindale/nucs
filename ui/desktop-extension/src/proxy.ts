// NUCS for Claude Desktop: a local MCP server that forwards to a NUCS server's /mcp.
//
// Claude Desktop runs this on the person's own computer (it is an .mcpb extension) and talks
// to it over stdio. Every tool and prompt NUCS offers is listed and forwarded as is, so a new
// tool on the server needs no new extension. The one thing that cannot go through the server
// is reading this computer's files, so import_from_disk lives here: it reads a folder, zip or
// .md file from the folders the person allowed, and uploads the bytes straight to NUCS's
// intake. Nothing binary ever passes through Claude.
import { readdirSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, extname, join, relative, resolve, sep } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import {
  CallToolRequestSchema, GetPromptRequestSchema, ListPromptsRequestSchema, ListToolsRequestSchema,
  type CallToolResult, type Tool,
} from '@modelcontextprotocol/sdk/types.js';
import { zipSync } from 'fflate';

export const VERSION = '1.0.0';
const UPLOAD_MAX = 200 * 1024 * 1024;
const SKIP = /^(\.|__MACOSX$|Thumbs\.db$|desktop\.ini$)/;

export interface ProxyOptions {
  /** The NUCS server, e.g. https://nucs.example.org (a trailing /mcp is accepted too). */
  url: string;
  token: string;
  /** Folders import_from_disk may read from. Empty: the Downloads folder. */
  folders: string[];
}

export const IMPORT_TOOL: Tool = {
  name: 'import_from_disk',
  title: 'Import module content from this computer',
  description: 'Reads module content a developer sent (a folder, a .zip, or a single .md file) from this computer and runs NUCS intake on it: '
    + 'topics are placed by their topic_id, checked against the course map, compared with what is stored, and validated. Images inside '
    + 'the folder or zip travel with it, and images embedded in the markdown are extracted by NUCS. check_only (the default) writes nothing; '
    + 'run it first and show the person the result. A topic that differs from the stored one is refused and its diff returned; pass '
    + 'replace only after the person has seen the diff and agreed. Only folders allowed in the extension settings can be read '
    + '(the Downloads folder by default).',
  inputSchema: {
    type: 'object',
    properties: {
      path: { type: 'string', description: 'Folder, .zip or .md file, e.g. ~/Downloads/KV7016-U04 or ~/Downloads/KV7016-U04.zip' },
      check_only: { type: 'boolean', default: true, description: 'true: report what would happen and write nothing' },
      replace: { type: 'boolean', default: false, description: 'overwrite topics that differ from the stored ones (only after the person agreed)' },
      scope: { type: 'string', description: 'optional module or unit to limit placement to, e.g. KV7016 or KV7016/U04' },
    },
    required: ['path'],
  },
  annotations: { destructiveHint: true },
};

export const SUBTITLES_TOOL: Tool = {
  name: 'upload_subtitles_from_disk',
  title: "Upload a topic's recorded SRT from this computer",
  description: "Uploads an .srt file from this computer as a topic's recorded subtitles in NUCS (edit/master.srt for English, "
    + 'edit/master.zh.srt for Mandarin), byte for byte: BOM and line endings are kept, nothing is retyped. Use it for the corrected '
    + 'SRT once the person has saved it and is happy with it. If the topic already has a different SRT, nothing is replaced and the '
    + 'difference is reported; pass replace only after the person has agreed to overwrite it. Only folders allowed in the '
    + 'extension settings can be read (the Downloads folder by default).',
  inputSchema: {
    type: 'object',
    properties: {
      topic: { type: 'string', description: 'Topic id, e.g. KV7015-U01-T03' },
      path: { type: 'string', description: 'The .srt file, e.g. ~/Downloads/KV7015-U01-T03_mp4.srt' },
      lang: { type: 'string', enum: ['en', 'zh'], default: 'en' },
      replace: { type: 'boolean', default: false, description: 'replace a different SRT already in NUCS (only after the person agreed)' },
    },
    required: ['topic', 'path'],
  },
  annotations: { destructiveHint: true },
};
const TOPIC_RE = /^[A-Z]{2}\d{4}-U\d{2}-T\d{2}$/;
const SRT_MAX = 5 * 1024 * 1024;

const expand = (p: string) => (p === '~' ? homedir() : p.startsWith(`~${sep}`) || p.startsWith('~/') ? join(homedir(), p.slice(2)) : p);

/** The allowed folders, resolved; unresolvable or unset ones dropped. */
export function allowedFolders(folders: string[]): string[] {
  const list = folders.map((f) => f.trim()).filter((f) => f && !f.startsWith('${'));
  const out: string[] = [];
  for (const f of list.length ? list : [join(homedir(), 'Downloads')]) {
    try { out.push(realpathSync(resolve(expand(f)))); } catch { /* not there: ignore */ }
  }
  return out;
}

/** `path` resolved and checked to be inside one of `folders`, or an Error explaining why not. */
export function checkPath(path: string, folders: string[]): string {
  let real: string;
  try { real = realpathSync(resolve(expand(path))); } catch { throw new Error(`${path} does not exist.`); }
  if (!folders.some((f) => real === f || real.startsWith(f + sep))) {
    throw new Error(`${path} is outside the folders NUCS may read (${folders.join(', ') || 'none'}). `
      + 'Move it into one of those, or add its folder in Claude Desktop: Settings → Extensions → NUCS → Configure.');
  }
  return real;
}

function walk(dir: string, base: string, out: Record<string, Uint8Array>, total: { n: number }): void {
  for (const name of readdirSync(dir)) {
    if (SKIP.test(name)) continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, base, out, total);
    else if (st.isFile()) {
      total.n += st.size;
      if (total.n > UPLOAD_MAX) throw new Error(`That is more than ${UPLOAD_MAX / 1024 / 1024} MB. Import it a unit at a time.`);
      out[relative(base, p).split(sep).join('/')] = readFileSync(p);
    }
  }
}

/** What gets uploaded: a zip as is, or a folder / .md file zipped up. */
export function packageSource(real: string): { name: string; bytes: Uint8Array; files: number } {
  const st = statSync(real);
  if (st.isFile()) {
    const ext = extname(real).toLowerCase();
    if (ext === '.zip') {
      if (st.size > UPLOAD_MAX) throw new Error(`That zip is more than ${UPLOAD_MAX / 1024 / 1024} MB.`);
      return { name: basename(real), bytes: readFileSync(real), files: 1 };
    }
    if (ext === '.md') return { name: `${basename(real, ext)}.zip`, bytes: zipSync({ [basename(real)]: readFileSync(real) }), files: 1 };
    throw new Error('Import a folder, a .zip or a .md file.');
  }
  const files: Record<string, Uint8Array> = {};
  walk(real, real, files, { n: 0 });
  const n = Object.keys(files).length;
  if (!Object.keys(files).some((f) => f.toLowerCase().endsWith('.md'))) throw new Error(`${real} has no .md files in it.`);
  return { name: `${basename(real)}.zip`, bytes: zipSync(files), files: n };
}

const textResult = (t: string, isError = false): CallToolResult => ({ content: [{ type: 'text', text: t }], ...(isError ? { isError: true } : {}) });

export function createProxy(o: ProxyOptions): Server {
  const base = o.url.trim().replace(/\/+$/, '').replace(/\/mcp$/, '');
  const auth = { Authorization: `Bearer ${o.token.trim()}` };
  const folders = allowedFolders(o.folders);
  let remote: Client | null = null;

  const explain = (e: unknown): Error => {
    const msg = (e as Error).message || String(e);
    if (/401|Unauthorized|not valid|revoked|token is required/i.test(msg)) {
      return new Error('NUCS did not accept the API token. In NUCS, open the account menu → Connect Claude Desktop, create a new token, '
        + 'and paste it into Claude Desktop: Settings → Extensions → NUCS → Configure.');
    }
    if (/fetch failed|ENOTFOUND|ECONNREFUSED|EAI_AGAIN|Invalid URL/i.test(msg)) {
      return new Error(`Could not reach NUCS at ${base}. Check the NUCS address in the extension settings and your network connection.`);
    }
    return e instanceof Error ? e : new Error(msg);
  };

  async function client(): Promise<Client> {
    if (remote) return remote;
    if (!base || !o.token.trim()) throw new Error('Set the NUCS address and API token in Claude Desktop: Settings → Extensions → NUCS → Configure.');
    const c = new Client({ name: 'nucs-desktop', version: VERSION });
    await c.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`), { requestInit: { headers: auth } }));
    remote = c;
    return c;
  }
  /** Runs `fn` against the server, reconnecting once if the connection went stale. */
  async function onRemote<T>(fn: (c: Client) => Promise<T>): Promise<T> {
    try {
      return await fn(await client());
    } catch (first) {
      remote = null;
      if (/did not accept|Could not reach|Set the NUCS/.test(explain(first).message)) throw explain(first);
      try { return await fn(await client()); } catch (e) { remote = null; throw explain(e); }
    }
  }

  async function importFromDisk(args: Record<string, unknown>): Promise<CallToolResult> {
    const path = typeof args.path === 'string' ? args.path : '';
    if (!path) return textResult('Give the path of a folder, .zip or .md file.', true);
    const checkOnly = args.check_only !== false;
    let pkg: ReturnType<typeof packageSource>;
    let real: string;
    try {
      real = checkPath(path, folders);
      pkg = packageSource(real);
    } catch (e) {
      return textResult((e as Error).message, true);
    }
    const q = new URLSearchParams({ name: pkg.name.replace(/[^A-Za-z0-9._-]/g, '_') });
    if (checkOnly) q.set('dry_run', '1');
    if (args.replace === true && !checkOnly) q.set('replace', '1');
    if (typeof args.scope === 'string' && args.scope.trim()) q.set('path', args.scope.trim());
    let res: Response;
    try {
      res = await fetch(`${base}/api/intake/upload?${q}`, {
        method: 'POST', headers: { ...auth, 'Content-Type': 'application/zip' }, body: pkg.bytes,
      });
    } catch (e) {
      return textResult(explain(e).message, true);
    }
    const body = await res.text();
    if (!res.ok) {
      let msg = body;
      try { msg = (JSON.parse(body) as { error?: string }).error ?? body; } catch { /* text */ }
      return textResult(res.status === 401 ? explain(new Error('401')).message : `NUCS refused the upload: ${msg}`, true);
    }
    const job = JSON.parse(body) as { id: number };
    const outcome = await onRemote((c) => c.callTool({ name: 'get_job_result', arguments: { job_id: job.id, wait_seconds: 240 } }));
    const head = `${checkOnly ? 'Checked (nothing written)' : 'Imported'}: ${real} (${pkg.files} file${pkg.files === 1 ? '' : 's'}), NUCS job ${job.id}.\n\n`;
    const content = (outcome.content as CallToolResult['content']).map((c) => (c.type === 'text' ? { ...c, text: head + c.text } : c));
    return { content, ...(outcome.isError ? { isError: true } : {}) };
  }

  async function uploadSubtitles(args: Record<string, unknown>): Promise<CallToolResult> {
    const topic = typeof args.topic === 'string' ? args.topic.trim() : '';
    const lang = args.lang === 'zh' ? 'zh' : 'en';
    if (!TOPIC_RE.test(topic)) return textResult('Give the topic id, e.g. KV7015-U01-T03.', true);
    let real: string;
    let bytes: Uint8Array;
    try {
      real = checkPath(typeof args.path === 'string' ? args.path : '', folders);
      if (extname(real).toLowerCase() !== '.srt' || !statSync(real).isFile()) throw new Error('Upload an .srt file.');
      if (statSync(real).size > SRT_MAX) throw new Error('That file is too large to be a subtitle file.');
      bytes = readFileSync(real);
    } catch (e) {
      return textResult((e as Error).message, true);
    }
    const q = new URLSearchParams({ kind: lang });
    if (args.replace === true) q.set('replace', '1');
    let res: Response;
    try {
      res = await fetch(`${base}/api/topic/${topic}/media?${q}`, { method: 'POST', headers: { ...auth, 'Content-Type': 'application/x-subrip' }, body: bytes });
    } catch (e) {
      return textResult(explain(e).message, true);
    }
    const body = await res.text();
    if (!res.ok) {
      let msg = body;
      try { msg = (JSON.parse(body) as { error?: string }).error ?? body; } catch { /* text */ }
      return textResult(res.status === 401 ? explain(new Error('401')).message : `NUCS refused the upload: ${msg}`, true);
    }
    const job = JSON.parse(body) as { id: number };
    const outcome = await onRemote((c) => c.callTool({ name: 'get_job_result', arguments: { job_id: job.id, wait_seconds: 120 } }));
    const head = `Uploaded ${real} as ${topic}'s ${lang === 'zh' ? 'Mandarin' : 'English'} recorded SRT (NUCS job ${job.id}).\n\n`;
    const content = (outcome.content as CallToolResult['content']).map((c) => (c.type === 'text' ? { ...c, text: head + c.text } : c));
    return { content, ...(outcome.isError ? { isError: true } : {}) };
  }

  const server = new Server({ name: 'nucs', version: VERSION }, {
    capabilities: { tools: {}, prompts: {} },
    instructions: 'NUCS is the production system for the programme\'s modules, units and topics. Use its tools as the ground truth, '
      + 'and say what you are about to write before writing anything.',
  });

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    const { tools } = await onRemote((c) => c.listTools());
    return { tools: [...tools, IMPORT_TOOL, SUBTITLES_TOOL] };
  });
  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    if (req.params.name === IMPORT_TOOL.name) return importFromDisk(req.params.arguments ?? {});
    if (req.params.name === SUBTITLES_TOOL.name) return uploadSubtitles(req.params.arguments ?? {});
    try {
      return await onRemote((c) => c.callTool(req.params)) as CallToolResult;
    } catch (e) {
      return textResult((e as Error).message, true);
    }
  });
  server.setRequestHandler(ListPromptsRequestSchema, async () => onRemote((c) => c.listPrompts()));
  server.setRequestHandler(GetPromptRequestSchema, async (req) => onRemote((c) => c.getPrompt(req.params)));
  return server;
}
