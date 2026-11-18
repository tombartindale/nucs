// The MCP endpoint: NUCS as a set of tools for Claude (Claude Desktop, through the NUCS
// extension in ui/desktop-extension/, which forwards every tool here).
//
// Every tool is a thin wrapper over the app's own HTTP API, called in-process with
// fastify.inject(): the same validation, the same bcn calls and caches, the same rules
// (only the author deletes a comment, intake never overwrites a differing topic without
// --replace). Nothing here decides anything the API does not already decide. Injected
// requests carry a per-process secret (server.ts) that marks them as already authenticated
// as the token's owner and as coming from Claude, so writes are attributed to that person
// and flagged "via Claude".
//
// Stateless Streamable HTTP: a new server and transport per POST, JSON responses, no
// sessions. Nothing here needs a server-to-client stream.
import type { IncomingMessage } from 'node:http';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import type { FastifyInstance, FastifyRequest } from 'fastify';
import { z } from 'zod';
import type {
  AnyEnvelope, DiagnosticsEnvelope, JobDetail, JobSummary, QaItem, QaItemResponse, QaListResponse, ShowEnvelope, StatusEnvelope, TopicStatus,
} from '@beacon/shared';

export const INTERNAL_HEADER = 'x-nucs-internal';
export const INTERNAL_AS_HEADER = 'x-nucs-as';
const BODY_MAX = 5 * 1024 * 1024;
const FINISHED = new Set(['done', 'failed', 'cancelled', 'interrupted']);
const VERSION = '1.0.0';

const TOPIC_ID = z.string().regex(/^[A-Z]{2}\d{4}-U\d{2}-T\d{2}$/).describe('Topic id, e.g. KV7015-U01-T03');
const MODULE_ID = z.string().regex(/^[A-Z]{2}\d{4}$/).describe('Module code, e.g. KV7015');
const QA_ITEM_ID = z.string().describe("QA item id: 'video:<topic id>' (a topic's composed Mandarin video) or 'activity:<module>/<unit>' (a unit's Mandarin activity), e.g. 'video:KV7015-U01-T03' or 'activity:KV7015/U01'");
const LANG = z.enum(['en', 'zh']).default('en').describe('en (English) or zh (Mandarin)');

class ToolError extends Error {}

/** Calls the app's own API as `email`, via Claude. Throws ToolError with the API's own message. */
type Call = <T>(method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE', url: string, payload?: unknown) => Promise<T>;

function text(value: unknown) {
  return { content: [{ type: 'text' as const, text: typeof value === 'string' ? value : JSON.stringify(value, null, 1) }] };
}

/** Wraps a tool body so an API refusal comes back to Claude as a readable tool error, not a protocol failure. */
function tool<A>(fn: (args: A) => Promise<unknown>) {
  return async (args: A) => {
    try {
      return text(await fn(args));
    } catch (e) {
      return { ...text(e instanceof ToolError ? e.message : `NUCS error: ${(e as Error).message}`), isError: true };
    }
  };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function lang(t: TopicStatus['en'] | undefined) {
  if (!t) return null;
  return { stage: t.stage, next: t.next, blocked: t.blocked, blockers: t.blockers.map((b) => b.message), stale: t.stale, diagnostics: t.diagnostics };
}

function diag(d: AnyEnvelope['diagnostics'][number]) {
  const x = d as unknown as Record<string, unknown>;
  const out: Record<string, unknown> = { level: d.level, code: d.code, message: d.message };
  for (const k of ['topic', 'lang', 'file', 'line', 'slide', 'hint']) if (x[k] !== undefined && x[k] !== null && x[k] !== '') out[k] = x[k];
  return out;
}

/** A finished job, reduced to what Claude needs: did it work, and what did bcn say. */
function jobOutcome(job: JobDetail) {
  return {
    job_id: job.id, state: job.state, ok: job.ok, label: job.label,
    envelopes: job.envelopes.map((e) => ({
      tool: e.tool, ok: e.ok, target: e.target,
      results: e.results,
      diagnostics: e.diagnostics.map(diag),
    })),
  };
}

function buildServer(call: Call): McpServer {
  const s = new McpServer({ name: 'nucs', version: VERSION }, {
    instructions: 'NUCS is the production system for the MSc Applied AI (BEACON) programme: modules (e.g. KV7015) of units (U01..) of topics '
      + '(KV7015-U01-T01), each a narrated slide video authored as topic.md (Marp slides with "> **Say:**" narration blocks), with a Mandarin '
      + 'track (topic.zh.md, Mandarin subtitles, Mandarin videos). Treat what these tools return as the ground truth. QA items are the Mandarin '
      + 'deliverables awaiting internal review. Anything you write (QA comments, intake) is recorded as the signed-in person, marked "via Claude"; '
      + 'say what you are about to write before writing it.',
  });

  const status = async () => call<StatusEnvelope>('GET', '/api/status');
  const awaitJob = async (id: number, waitSeconds: number) => {
    const deadline = Date.now() + waitSeconds * 1000;
    for (;;) {
      const job = await call<JobDetail>('GET', `/api/jobs/${id}`);
      if (FINISHED.has(job.state)) return jobOutcome(job);
      if (Date.now() > deadline) return { job_id: id, state: job.state, finished: false, note: 'Still running. Call get_job_result again with this job_id.' };
      await sleep(500);
    }
  };

  // -- reading ---------------------------------------------------------------------------------
  s.registerTool('programme_overview', {
    title: 'Programme overview',
    description: 'Every module with its title, units, stage counts and module documents. With `module`, also every topic in it with its '
      + 'English and Mandarin stage, next step, blockers and diagnostic counts.',
    inputSchema: { module: MODULE_ID.optional() },
    annotations: { readOnlyHint: true },
  }, tool(async ({ module }: { module?: string }) => {
    const env = await status();
    const modules = Object.entries(env.summary.modules)
      .filter(([m]) => !module || m === module)
      .map(([m, sm]) => ({
        module: m, title: sm.title, units: sm.units.map((u) => ({ unit: u, title: sm.unit_titles[u] || '' })),
        topics: sm.topics, en_stages: sm.en, zh_stages: sm.zh, complete: sm.complete, blocked: sm.blocked, stale: sm.stale,
        course_map: sm.course_map,
        documents: sm.documents.map((d) => ({ path: d.path, exists: d.exists, errors: d.errors, warnings: d.warnings })),
      }));
    if (module && !modules.length) throw new ToolError(`No module ${module}.`);
    if (!module) return { modules };
    const topics = env.results.filter((t) => t.module === module).map((t) => ({
      topic: t.topic, unit: t.unit, title: t.title, minutes: t.minutes, outcomes: t.outcomes, en: lang(t.en), zh: lang(t.zh),
    }));
    return { ...modules[0], topic_list: topics };
  }));

  s.registerTool('get_topic', {
    title: 'Topic script and state',
    description: "A topic's parsed script: per slide, the title, the narration (the Say block) and word counts, plus the cue times, "
      + 'which subtitle files exist, and its English/Mandarin pipeline state. Use get_topic_source for the raw markdown.',
    inputSchema: { topic: TOPIC_ID, lang: LANG },
    annotations: { readOnlyHint: true },
  }, tool(async ({ topic, lang: l }: { topic: string; lang: 'en' | 'zh' }) => {
    const r = await call<{ show: ShowEnvelope; status: TopicStatus | null }>('GET', `/api/topic/${topic}`);
    const show = r.show.results[0];
    const script = show?.[l] ?? null;
    return {
      topic, title: r.status?.title, minutes: r.status?.minutes, outcomes: r.status?.outcomes,
      state: r.status ? { en: lang(r.status.en), zh: lang(r.status.zh) } : null,
      script: script && {
        words: script.words, target_words: script.target_words, words_ok: script.words_ok,
        slides: script.slides.map((sl) => ({ slide: sl.index, title: sl.title, narration: sl.narration, words: sl.words, words_ok: sl.words_ok, images: sl.images })),
      },
      cues: show?.cues ?? [],
      subtitles: show?.media.subtitles ?? null,
    };
  }));

  s.registerTool('get_topic_source', {
    title: 'Topic markdown',
    description: 'The raw topic.md (en) or topic.zh.md (zh) exactly as stored: front matter, Marp slides separated by ---, and "> **Say:**" narration blocks.',
    inputSchema: { topic: TOPIC_ID, lang: LANG },
    annotations: { readOnlyHint: true },
  }, tool(async ({ topic, lang: l }: { topic: string; lang: 'en' | 'zh' }) => {
    const r = await call<{ exists: boolean; text: string; path: string }>('GET', `/api/topic/${topic}/source?lang=${l}`);
    if (!r.exists) throw new ToolError(`${r.path} does not exist yet.`);
    return r.text;
  }));

  s.registerTool('read_document', {
    title: 'Module document',
    description: 'A module document as stored, by path: KV7015/course-map.md, KV7015/assets.md (asset requests), KV7015/reading-list.md, '
      + 'KV7015/assignment-1.md, KV7015/U03/activity.md, or the Mandarin versions (activity.zh.md, assignment-1.zh.md). '
      + 'programme_overview(module) lists which exist.',
    inputSchema: { path: z.string().describe('e.g. KV7015/course-map.md') },
    annotations: { readOnlyHint: true },
  }, tool(async ({ path }: { path: string }) => {
    const r = await call<{ exists: boolean; text: string; path: string }>('GET', `/api/moddoc?path=${encodeURIComponent(path)}`);
    if (!r.exists) throw new ToolError(`${r.path} does not exist.`);
    return r.text;
  }));

  s.registerTool('get_subtitles', {
    title: 'Subtitles',
    description: "A topic's subtitles as SRT text: the aligned subtitles if bcn subtitles has run, otherwise the editor's source SRT.",
    inputSchema: { topic: TOPIC_ID, lang: LANG },
    annotations: { readOnlyHint: true },
  }, tool(async ({ topic, lang: l }: { topic: string; lang: 'en' | 'zh' }) => {
    const env = await status();
    const t = env.results.find((r) => r.topic === topic);
    if (!t) throw new ToolError(`No topic ${topic}.`);
    const pick = t.artifacts.find((a) => a.kind === 'subtitles' && a.lang === l && a.exists)
      ?? t.artifacts.find((a) => a.kind === 'source' && a.lang === l && a.exists && a.path.endsWith('.srt'));
    if (!pick) throw new ToolError(`${topic} has no ${l === 'zh' ? 'Mandarin' : 'English'} subtitles yet.`);
    const body = await call<string>('GET', `/files/${pick.path}?view=1`);
    return `# ${pick.path}\n\n${body}`;
  }));

  s.registerTool('get_diagnostics', {
    title: 'Diagnostics',
    description: 'Every outstanding diagnostic (validation errors, warnings, suspected mis-transcriptions) under a module, unit or topic path, or the whole programme.',
    inputSchema: { path: z.string().optional().describe('e.g. KV7015, KV7015/U02 or KV7015/U02/T05; omit for everything') },
    annotations: { readOnlyHint: true },
  }, tool(async ({ path }: { path?: string }) => {
    const env = await call<DiagnosticsEnvelope>('GET', `/api/diagnostics?path=${encodeURIComponent(path || '.')}`);
    return { counts: env.counts, outstanding: env.outstanding.map(diag) };
  }));

  // -- QA ----------------------------------------------------------------------------------------
  s.registerTool('list_qa_items', {
    title: 'QA items',
    description: 'The Mandarin deliverables ready for internal QA, with each one\'s verdict (to_review, changes_needed, approved), '
      + 'whether the file changed since its verdict, and comment counts.',
    inputSchema: {
      module: MODULE_ID.optional(),
      status: z.enum(['to_review', 'changes_needed', 'approved']).optional(),
      needs_attention: z.boolean().optional().describe('only items to review or changed since their verdict'),
    },
    annotations: { readOnlyHint: true },
  }, tool(async ({ module, status: st, needs_attention }: { module?: string; status?: string; needs_attention?: boolean }) => {
    const { items } = await call<QaListResponse>('GET', '/api/qa');
    return items
      .filter((i) => (!module || i.module === module) && (!st || i.status === st)
        && (!needs_attention || i.status === 'to_review' || i.changedSinceReview))
      .map((i: QaItem) => ({
        item: i.item, type: i.type, title: i.title, module: i.module, unit: i.unit, topic: i.topic, path: i.path,
        status: i.status, changed_since_review: i.changedSinceReview, stale: i.stale, open_comments: i.openComments, comments: i.comments,
      }));
  }));

  s.registerTool('get_qa_item', {
    title: 'QA item',
    description: 'One QA item with its verdict and every comment (id, text, kind, anchor, author, resolved). For a video, read its script '
      + 'with get_topic / get_topic_source and its Mandarin subtitles with get_subtitles(lang zh); for an activity, read_document its path '
      + 'and the English activity.md beside it.',
    inputSchema: { item: QA_ITEM_ID },
    annotations: { readOnlyHint: true },
  }, tool(async ({ item }: { item: string }) => call<QaItemResponse>('GET', `/api/qa/item/${encodeURIComponent(item)}`)));

  s.registerTool('add_qa_comment', {
    title: 'Add QA comment',
    description: 'Adds a comment or suggestion to a QA item, recorded as the signed-in person and marked "via Claude". Anchor it to a '
      + 'time in the video (seconds) or a line of the activity where you can. A suggestion should give the replacement text.',
    inputSchema: {
      item: QA_ITEM_ID,
      body: z.string().min(1).max(5000),
      kind: z.enum(['comment', 'suggestion']).default('comment'),
      anchor_seconds: z.number().min(0).optional().describe('video items: seconds into the video'),
      anchor_line: z.number().int().min(1).optional().describe('activity items: line number in the .zh.md'),
    },
  }, tool(async (a: { item: string; body: string; kind: string; anchor_seconds?: number; anchor_line?: number }) => {
    const r = await call<QaItemResponse>('POST', `/api/qa/item/${encodeURIComponent(a.item)}/comments`,
      { body: a.body, kind: a.kind, anchorT: a.anchor_seconds ?? null, anchorLine: a.anchor_line ?? null });
    return { added: r.comments[r.comments.length - 1], item: r.item };
  }));

  s.registerTool('resolve_qa_comment', {
    title: 'Resolve QA comment',
    description: 'Marks a QA comment resolved (or reopens it).',
    inputSchema: { comment_id: z.number().int(), resolved: z.boolean().default(true) },
  }, tool(async ({ comment_id, resolved }: { comment_id: number; resolved: boolean }) =>
    (await call<QaItemResponse>('PATCH', `/api/qa/comments/${comment_id}`, { resolved })).item));

  s.registerTool('set_qa_status', {
    title: 'Set QA verdict',
    description: "Sets a QA item's verdict: to_review, changes_needed or approved. Only do this when the person has asked for it.",
    inputSchema: { item: QA_ITEM_ID, status: z.enum(['to_review', 'changes_needed', 'approved']) },
  }, tool(async ({ item, status: st }: { item: string; status: string }) =>
    (await call<QaItemResponse>('PUT', `/api/qa/item/${encodeURIComponent(item)}/status`, { status: st })).item));

  // -- intake ------------------------------------------------------------------------------------
  const intake = async (a: { text: string; path?: string; replace?: boolean }, dryRun: boolean) => {
    const job = await call<JobSummary>('POST', '/api/intake', { text: a.text, path: a.path || '.', dry_run: dryRun, replace: !!a.replace });
    return awaitJob(job.id, 120);
  };
  const INTAKE_TEXT = z.string().min(1).describe('One or more topic.md files concatenated (each starts with its own front matter: topic_id, title, minutes, lang)');
  const INTAKE_PATH = z.string().optional().describe('Limit to a module or unit, e.g. KV7016 or KV7016/U04; omit to place by topic_id');

  s.registerTool('intake_check', {
    title: 'Check topic content (no changes)',
    description: 'Dry run of intake for pasted topic markdown: which topics it holds, whether each is in the course map, whether it would be '
      + 'written, is unchanged, or differs from the stored topic.md (with a diff), and every validation diagnostic. Writes nothing. '
      + 'For files on disk (zips, folders, images), use import_from_disk instead.',
    inputSchema: { text: INTAKE_TEXT, path: INTAKE_PATH },
    annotations: { readOnlyHint: true },
  }, tool(async (a: { text: string; path?: string }) => intake(a, true)));

  s.registerTool('intake_submit', {
    title: 'Import topic content',
    description: 'Places pasted topic markdown into the programme and validates it (the same as the Intake page). A topic whose stored '
      + 'topic.md differs is refused and its diff returned; pass replace=true only after the person has seen that diff and agreed. '
      + 'Run intake_check first and show the person the result.',
    inputSchema: { text: INTAKE_TEXT, path: INTAKE_PATH, replace: z.boolean().default(false) },
    annotations: { destructiveHint: true },
  }, tool(async (a: { text: string; path?: string; replace?: boolean }) => intake(a, false)));

  s.registerTool('get_job_result', {
    title: 'Job result',
    description: 'Waits for a NUCS job (e.g. an intake started by import_from_disk) and returns its outcome and diagnostics.',
    inputSchema: { job_id: z.number().int(), wait_seconds: z.number().int().min(0).max(300).default(120) },
    annotations: { readOnlyHint: true },
  }, tool(async ({ job_id, wait_seconds }: { job_id: number; wait_seconds: number }) => awaitJob(job_id, wait_seconds)));

  // -- prompts -----------------------------------------------------------------------------------
  const user = (t: string) => ({ messages: [{ role: 'user' as const, content: { type: 'text' as const, text: t } }] });

  s.registerPrompt('review_qa_item', {
    title: 'Review a QA item',
    description: 'Review one Mandarin deliverable against its English source and leave anchored comments.',
    argsSchema: { item: z.string().describe("e.g. video:KV7015-U01-T03 or activity:KV7015/U01") },
  }, ({ item }) => user(
    `Review the NUCS QA item ${item}.\n\n`
    + `1. get_qa_item to see its verdict and the comments already made; don't repeat those.\n`
    + `2. For a video: get_topic_source (en and zh) and get_subtitles (zh), and compare the Mandarin narration and subtitles with the English `
    + `script slide by slide. For an activity: read_document the .zh.md and the English activity.md beside it.\n`
    + `3. Look for: meaning changed or lost, untranslated or machine-literal phrasing, inconsistent terminology across the module, `
    + `subtitle text that does not match the narration, and anything a Mandarin-speaking student would stumble on.\n`
    + `4. List what you found, most serious first, each with its anchor (video seconds from the subtitle timings, or activity line) `
    + `and, where you can, replacement text.\n`
    + `5. Ask me which to post, then add_qa_comment for those (kind suggestion when you give replacement text). `
    + `Don't change the verdict unless I ask.`));

  s.registerPrompt('reconcile_module', {
    title: 'Reconcile a module',
    description: 'Compare the course map with what was built, and check assessment alignment.',
    argsSchema: { module: z.string().describe('e.g. KV7015') },
  }, ({ module }) => user(
    `Reconcile NUCS module ${module}.\n\n`
    + `1. read_document ${module}/course-map.md and programme_overview(${module}).\n`
    + `2. Compare the map with the built topics: topics missing or extra, retitled, reordered, and planned vs actual minutes per unit `
    + `(sum the topics' minutes).\n`
    + `3. Check outcome coverage: every learning outcome in the map is covered by at least one built topic.\n`
    + `4. read_document each assignment brief and unit activity listed, and check: deadlines spread across the module, components that `
    + `build on each other, the portfolio introduced early, and no narration that commits to marking criteria (search the topic sources `
    + `where needed).\n`
    + `5. Report a table of discrepancies, each with where it is and a suggested fix. Change nothing.`));

  s.registerPrompt('import_tutor_content', {
    title: 'Import tutor content',
    description: 'Check and import topic content a module developer has sent (a folder, zip or .md files on this computer).',
    argsSchema: { path: z.string().describe('folder, .zip or .md file, e.g. ~/Downloads/KV7016-U04') },
  }, ({ path }) => user(
    `Import the module content at ${path} into NUCS.\n\n`
    + `1. import_from_disk(path: "${path}", check_only: true). Embedded images in the markdown are extracted by NUCS itself.\n`
    + `2. Summarise for me: topics found and where they go, any not in the course map, any that differ from what is stored `
    + `(show the diffs), and the validation errors and warnings by topic.\n`
    + `3. Format-only problems I could fix by re-sending are worth naming, but never rewrite the developer's wording yourself.\n`
    + `4. Ask me before importing. Then import_from_disk(path: "${path}", check_only: false). Only use replace: true for topics I `
    + `have explicitly agreed to overwrite after seeing their diff.\n`
    + `5. Report what was written and what is left to do.`));

  return s;
}

/** Reads the raw request body as JSON (any JSON value: MCP messages may be objects or arrays). */
async function readBody(req: FastifyRequest): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req.body as IncomingMessage ?? []) {
    size += chunk.length;
    if (size > BODY_MAX) throw new Error('Request too large.');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || 'null');
}

export function registerMcpRoutes(f: FastifyInstance, internalSecret: string): void {
  f.post('/mcp', async (req, reply) => {
    const email = (req as FastifyRequest & { email?: string }).email;
    if (!email) return reply.code(401).send({ error: 'A NUCS API token is required.' });
    let body: unknown;
    try { body = await readBody(req); } catch (e) {
      return reply.code(400).send({ jsonrpc: '2.0', error: { code: -32700, message: (e as Error).message }, id: null });
    }
    const call: Call = async <T>(method: string, url: string, payload?: unknown): Promise<T> => {
      const res = await f.inject({
        method: method as 'GET', url,
        headers: {
          [INTERNAL_HEADER]: internalSecret, [INTERNAL_AS_HEADER]: email,
          ...(payload === undefined ? {} : { 'content-type': 'application/json' }),
        },
        payload: payload === undefined ? undefined : JSON.stringify(payload),
      });
      let data: unknown = res.body;
      if ((res.headers['content-type'] || '').toString().includes('application/json')) {
        try { data = JSON.parse(res.body); } catch { /* leave as text */ }
      }
      if (res.statusCode >= 400) {
        const msg = data && typeof data === 'object' && 'error' in data ? String((data as { error: unknown }).error) : `HTTP ${res.statusCode}`;
        throw new ToolError(msg);
      }
      return data as T;
    };
    const server = buildServer(call);
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    reply.hijack();
    reply.raw.on('close', () => { void transport.close(); void server.close(); });
    await server.connect(transport);
    await transport.handleRequest(req.raw, reply.raw, body);
  });
  const notAllowed = { jsonrpc: '2.0', error: { code: -32000, message: 'Method not allowed: this endpoint is stateless; POST only.' }, id: null };
  f.get('/mcp', async (_req, reply) => reply.code(405).header('Allow', 'POST').send(notAllowed));
  f.delete('/mcp', async (_req, reply) => reply.code(405).header('Allow', 'POST').send(notAllowed));
}
