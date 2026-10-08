// HTTP server: JSON API, server-sent events, the app, and files from the root.
//
// Host/Origin are still checked (so a foreign web page cannot drive this API from a
// victim's browser via CSRF-style requests), but the allowed hostnames are configurable
// via ALLOWED_HOSTS, since this now typically runs behind a reverse proxy on a real
// hostname rather than only ever being reached at localhost. Login (see auth.ts) is the
// actual access control; this check is a defense-in-depth CSRF guard, not the only gate.
import { createReadStream, createWriteStream, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import type { IncomingMessage } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { basename, extname, join, relative, resolve, sep } from 'node:path';
import { pipeline } from 'node:stream/promises';
import cookie from '@fastify/cookie';
import extractZip from 'extract-zip';
import Fastify, { type FastifyInstance, type FastifyReply, type FastifyRequest } from 'fastify';
import type {
  BcnDiagnosticsEnvelope, BcnDoceditEnvelope, BcnEditEnvelope, BcnReviewEnvelope, BcnStatusEnvelope, BcnSyncEnvelope, BcnThemecheckEnvelope,
  BootResponse, JobArgs, ModulePlanRemindResponse, ModulePlanRequest, ModulePlanResponse, PlansSummaryResponse, ShowEnvelope, TranslationItem,
} from '@beacon/shared';
import { jobLabel, type App } from './app.js';
import { SESSION_COOKIE } from './auth.js';
import { Backups } from './backups.js';
import { BadRequest, BcnError, Forbidden } from './errors.js';
import { buildIcs, type IcsEvent } from './ics.js';
import { mimeType, withCharset } from './mime.js';
import { computeModulePlan, moduleTopicsFor } from './planning.js';
import { now, pyJson, sha256, stamp } from './util.js';

const BODY_MAX = 5 * 1024 * 1024;
const UPLOAD_MAX = 500 * 1024 * 1024;
// A whole-programme --full backup can run to many GB of video across every module; the
// other uploads (a handover batch, a translation return) stay at UPLOAD_MAX, so only the
// one route that needs it pays for a bigger Fastify bodyLimit override.
const TRANSFER_UPLOAD_MAX = 20 * 1024 * 1024 * 1024;
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);
const TOPIC = '^[A-Z]{2}\\d{4}-U\\d{2}-T\\d{2}$';
const MODULE = '^[A-Z]{2}\\d{4}$';
// The feed's secret lives in the path, not a query string: some calendar clients
// (desktop Outlook among them) silently drop query parameters when subscribing to an
// internet calendar, which otherwise turns every ICS fetch into a 404.
const ICS_PATH = /^\/api\/module\/[A-Z]{2}\d{4}\/plan\/[^/]+\/calendar\.ics$/;
const PING_MS = 15_000;
const ADMIN_EMAILS = new Set((process.env.ADMIN_EMAILS || '').split(',').map((e) => e.trim().toLowerCase()).filter(Boolean));
const SSE_BACKLOG = 4 * 1024 * 1024;  // a tab that falls this far behind is dropped; it resyncs on reconnect

/** localhost, plus any hostnames in ALLOWED_HOSTS (comma-separated, e.g. beacon.example.org). */
const ALLOWED_HOSTS = new Set([...LOCAL_HOSTS, ...(process.env.ALLOWED_HOSTS || '').split(',').map((h) => h.trim()).filter(Boolean)]);

type Query = Record<string, string | undefined>;
type Req = FastifyRequest<{ Params: Record<string, string>; Querystring: Query }>;

function hostname(value: string): string {
  if (value.startsWith('[')) return value.slice(0, value.indexOf(']') + 1);
  return value.split(':')[0];
}

function allowed(req: FastifyRequest): boolean {
  if (!ALLOWED_HOSTS.has(hostname(req.headers.host || ''))) return false;
  const origin = req.headers.origin;
  if (origin) {
    try {
      if (!ALLOWED_HOSTS.has(new URL(origin).hostname)) return false;
    } catch { return false; }
  }
  return true;
}

/** Every body arrives as a stream; routes read it as JSON, or stream it (uploads). */
async function readJson(req: FastifyRequest): Promise<Record<string, unknown>> {
  const n = Number(req.headers['content-length'] || 0);
  if (n > BODY_MAX) throw new BadRequest('Request too large.');
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req.body as IncomingMessage ?? []) {
    size += chunk.length;
    if (size > BODY_MAX) throw new BadRequest('Request too large.');
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString('utf8');
  let data: unknown;
  try { data = JSON.parse(text || '{}'); } catch (e) { throw new BadRequest((e as Error).message); }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new BadRequest('Expected a JSON object.');
  return data as Record<string, unknown>;
}

function toInt(value: unknown, name: string): number {
  const n = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN;
  if (!Number.isFinite(n)) throw new BadRequest(`'${name}' must be a whole number.`);
  return Math.trunc(n);
}
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export interface ServerOptions {
  app: App;
  staticDir?: string;
  /** Test-only: skips the session check entirely. Never read from an environment
   *  variable, so it can only be turned on by a test harness passing it explicitly,
   *  never by an environment misconfiguration in a real deployment. */
  testDisableAuth?: boolean;
}

// Reachable without a session: the login flow itself, and the SPA shell/static assets
// (the app needs to load far enough to show a login form). Every other /api/* route
// requires a valid session cookie.
const PUBLIC_PATHS = new Set(['/api/auth/request-link', '/api/auth/verify', '/api/auth/logout', '/api/auth/me']);

export async function buildServer({ app, staticDir, testDisableAuth }: ServerOptions): Promise<FastifyInstance> {
  // Live event streams never finish by themselves, so closing must not wait for them.
  const f = Fastify({ logger: false, exposeHeadRoutes: true, bodyLimit: UPLOAD_MAX, forceCloseConnections: true });
  // Node's http.Server defaults requestTimeout to 5 minutes (a slow-loris mitigation): any
  // request whose body is not fully received by then is silently aborted, truncating
  // whatever had arrived rather than erroring clearly. A large upload (video, a full-backup
  // zip) on a slow connection can easily take longer than that, so it is disabled here —
  // every upload route already bounds size with its own bodyLimit, and every route needs a
  // session cookie except the handful of public auth/static paths.
  f.server.requestTimeout = 0;
  await f.register(cookie);
  const streams = new Set<import('node:http').ServerResponse>();
  f.addHook('onClose', async () => { for (const res of streams) res.end(); });
  f.removeAllContentTypeParsers();
  f.addContentTypeParser('*', (_req, payload, done) => done(null, payload));

  f.addHook('onRequest', async (req, reply) => {
    if (!allowed(req)) return reply.code(403).send({ error: 'Requests are accepted from localhost only.' });
    reply.header('Cache-Control', 'no-store');
    reply.header('X-Content-Type-Options', 'nosniff');
    const path = (req.raw.url || '').split('?')[0];
    // The ICS feed is fetched by calendar clients (Outlook, Apple Calendar), which cannot
    // send a session cookie. It carries its own per-module secret token instead, checked
    // inside the route handler itself — this carve-out is a single literal route shape and
    // opens nothing else under /api/*.
    const guarded = !testDisableAuth
      && (path.startsWith('/files/') || (path.startsWith('/api/') && !PUBLIC_PATHS.has(path) && !ICS_PATH.test(path)));
    if (guarded) {
      const session = await app.auth.session(req.cookies[SESSION_COOKIE]);
      if (!session) return reply.code(401).send({ error: 'Sign in required.' });
      (req as FastifyRequest & { email?: string }).email = session.email;
    }
  });

  f.setErrorHandler((err: Error & { statusCode?: number }, _req, reply) => {
    if (err instanceof BadRequest) return reply.code(400).send({ error: err.message });
    if (err instanceof Forbidden) return reply.code(403).send({ error: err.message });
    if (err instanceof BcnError) return reply.code(502).send({ error: err.message });
    if (err.statusCode && err.statusCode < 500) return reply.code(err.statusCode).send({ error: err.message });
    return reply.code(500).send({ error: err.message || 'Internal error.' });
  });

  f.setNotFoundHandler((_req, reply) => reply.code(404).send({ error: 'Not found.' }));

  // -- login (email magic link) ----------------------------------------------------------------
  const cookieOpts = { path: '/', httpOnly: true, sameSite: 'lax' as const, secure: app.auth.cookieSecure };

  f.post('/api/auth/request-link', async (req) => {
    const data = await readJson(req);
    const email = typeof data.email === 'string' ? data.email : '';
    await app.auth.requestLink(email);
    // Deliberately the same response whether or not the address is allowed or exists, so
    // this endpoint cannot be used to enumerate valid addresses.
    return { ok: true };
  });

  f.get('/api/auth/verify', async (req: Req, reply) => {
    const token = String(req.query.token || '');
    const sessionToken = await app.auth.verify(token);
    if (!sessionToken) return reply.type('text/html; charset=utf-8').code(400)
      .send('<!doctype html><title>Sign-in link expired</title><p>This sign-in link is invalid or has expired. '
        + 'Go back and request a new one.</p>');
    reply.setCookie(SESSION_COOKIE, sessionToken, cookieOpts);
    return reply.redirect('/');
  });

  f.get('/api/auth/me', async (req: Req) => {
    const session = await app.auth.session(req.cookies[SESSION_COOKIE]);
    return { email: session?.email ?? null };
  });

  f.post('/api/auth/logout', async (req: Req, reply) => {
    await app.auth.logout(req.cookies[SESSION_COOKIE]);
    reply.clearCookie(SESSION_COOKIE, { path: '/' });
    return { ok: true };
  });

  // -- API ------------------------------------------------------------------------------------
  f.get('/api/boot', async (req: Req): Promise<BootResponse> => ({
    root: app.root, prefs: await app.db.prefs(), operator: await app.operator(), warnings: app.warnings, admin: isAdmin(req),
    doctor: app.doctor, codes: app.codes, status_version: app.status.version,
  }));

  f.get('/api/status', async (_req, reply) => {
    const env = await app.status.get();
    if (!env) return reply.code(503).send({ error: app.status.error || 'Status is not available yet.' });
    return { ...env, version: app.status.version, jobs_running: await app.jobs.running() };
  });

  f.post('/api/status/refresh', async () => {
    app.clearQueryCache();
    await app.status.refresh('requested');
    return { version: app.status.version };
  });

  f.get(`/api/topic/:id(${TOPIC})`, async (req: Req) => {
    const id = req.params.id;
    const rel = app.targetRel(id);
    const path = app.targetPath(rel);
    if (req.query.verify) return app.bcn.query<BcnStatusEnvelope>('status', [path, '--verify']);
    const show = await app.cachedQuery<ShowEnvelope>(`show|${rel}`, 30_000, 'show', [path, '--asset-prefix', '/files/']);
    const env = await app.status.get();
    const row = env?.results?.find((r) => r.topic === id) ?? null;
    return { show, status: row };
  });

  // Script editing: bcn edit does the writing and checking; this only moves text.
  const editTextFile = (text: string): string => {
    const d = join(app.dataDir, 'edits');
    mkdirSync(d, { recursive: true });
    const old = readdirSync(d).filter((n) => n.endsWith('.md')).sort();
    for (const name of old.slice(0, Math.max(0, old.length - 50))) {
      try { unlinkSync(join(d, name)); } catch { /* already gone */ }
    }
    const file = join(d, `${stamp()}.md`);
    writeFileSync(file, text, 'utf8');
    return file;
  };
  const langOf = (v: unknown) => (v === 'zh' ? 'zh' : 'en');

  f.get(`/api/topic/:id(${TOPIC})/source`, async (req: Req) => {
    const rel = app.targetRel(req.params.id);
    const lang = req.query.lang ?? 'en';
    if (lang !== 'en' && lang !== 'zh') throw new BadRequest('lang must be en or zh');
    const p = join(app.root, rel, lang === 'en' ? 'topic.md' : 'topic.zh.md');
    const relPath = relative(app.root, p);
    if (!existsSync(p) || !statSync(p).isFile()) return { exists: false, text: '', sha256: null, path: relPath };
    const data = readFileSync(p);
    return { exists: true, text: data.toString('utf8').replace(/^﻿/, ''), sha256: sha256(data), path: relPath };
  });

  // Validate unsaved text (bcn edit --dry-run). Read-only, so it runs directly rather than as a job.
  f.post(`/api/topic/:id(${TOPIC})/check`, async (req: Req) => {
    const rel = app.targetRel(req.params.id);
    const data = await readJson(req);
    const file = editTextFile(String(data.text ?? ''));
    try {
      return await app.bcn.query<BcnEditEnvelope>('edit', [app.targetPath(rel), '--from', file, '--lang', langOf(data.lang), '--dry-run'], 60_000);
    } finally {
      try { unlinkSync(file); } catch { /* fine */ }
    }
  });

  const saveEdit = async (rel: string, lang: 'en' | 'zh', file: string, expectSha: unknown, overwrite: unknown, operator: string) => {
    const args: JobArgs = { from: file, lang };
    if (expectSha && !overwrite) args.expect_sha = String(expectSha);
    return app.jobs.submit('edit', [rel], args, `edit ${lang === 'zh' ? 'topic.zh.md' : 'topic.md'} · ${rel}`, operator);
  };

  f.post(`/api/topic/:id(${TOPIC})/save`, async (req: Req, reply) => {
    const rel = app.targetRel(req.params.id);
    const data = await readJson(req);
    const lang = langOf(data.lang);
    const job = await saveEdit(rel, lang, editTextFile(String(data.text ?? '')), data.expect_sha, data.overwrite, await app.operator());
    return reply.code(202).send(job);
  });

  // -- module document editing (course-map.md, a unit's activity.md, assignment-N.md) ---------
  // bcn docedit is the sole authority on which relative names count as an editable module
  // document; this just splits "KV7016/course-map.md" into the module and the doc's path
  // relative to it, and keeps the whole thing inside the root (reusing app.safePath).
  function modDocPath(raw: string): { module: string; relDoc: string; abs: string; relPath: string } {
    const abs = app.safePath(raw);
    const relPath = relative(app.root, abs);
    const parts = relPath.split(sep);
    if (parts.length < 2 || !new RegExp(MODULE).test(parts[0])) throw new BadRequest(`'${raw}' is not a module document.`);
    return { module: parts[0], relDoc: parts.slice(1).join('/'), abs, relPath };
  }

  f.get('/api/moddoc', async (req: Req) => {
    const { abs, relPath } = modDocPath(String(req.query.path ?? ''));
    if (!existsSync(abs) || !statSync(abs).isFile()) return { exists: false, text: '', sha256: null, path: relPath };
    const data = readFileSync(abs);
    return { exists: true, text: data.toString('utf8').replace(/^﻿/, ''), sha256: sha256(data), path: relPath };
  });

  // Validate unsaved text (bcn docedit --dry-run). Read-only, so it runs directly rather than as a job.
  f.post('/api/moddoc/check', async (req: Req) => {
    const { module, relDoc } = modDocPath(String(req.query.path ?? ''));
    const data = await readJson(req);
    const file = editTextFile(String(data.text ?? ''));
    try {
      return await app.bcn.query<BcnDoceditEnvelope>('docedit', [app.targetPath(module), '--doc', relDoc, '--from', file, '--dry-run'], 60_000);
    } finally {
      try { unlinkSync(file); } catch { /* fine */ }
    }
  });

  f.post('/api/moddoc/save', async (req: Req, reply) => {
    const { module, relDoc } = modDocPath(String(req.query.path ?? ''));
    const data = await readJson(req);
    const file = editTextFile(String(data.text ?? ''));
    const args: JobArgs = { doc: relDoc, from: file };
    if (data.expect_sha && !data.overwrite) args.expect_sha = String(data.expect_sha);
    // The job's target is the specific doc, not the whole module: the scheduler's overlap
    // check (jobs.ts's overlaps()) treats one target as blocking another whenever one is a
    // path-prefix of the other, so a module-wide target would make saving course-map.md wait
    // behind any unrelated video job running on a topic underneath that module. docedit only
    // ever writes the one file named here, so only another job on that same file (or a
    // genuinely module-wide job, which does read module documents) needs to serialize with it.
    // scheduler.ts's argv() strips this back down to the module directory for the actual bcn
    // invocation, since docedit takes a module path positionally.
    const job = await app.jobs.submit('docedit', [`${module}/${relDoc}`], args, `edit ${relDoc} · ${module}`, await app.operator());
    return reply.code(202).send(job);
  });

  f.get('/api/diagnostics', async (req: Req) => {
    const rel = app.targetRel(req.query.path ?? '.');
    return app.cachedQuery<BcnDiagnosticsEnvelope>(`diagnostics|${rel}`, 10_000, 'diagnostics', [app.targetPath(rel)]);
  });

  // Both directions as dry runs. They only stat files, so no cloud download is triggered.
  f.get('/api/sync', async () => {
    const [pull, push] = await Promise.all([
      app.cachedQuery<BcnSyncEnvelope>('sync|pull', 20_000, 'sync', [app.root, '--pull', '--dry-run']),
      app.cachedQuery<BcnSyncEnvelope>('sync|push', 20_000, 'sync', [app.root, '--push', '--dry-run']),
    ]);
    return { pull, push };
  });

  f.get('/api/review', async (req: Req) => {
    const rel = app.targetRel(req.query.path ?? '.');
    return app.cachedQuery<BcnReviewEnvelope>(`review|${rel}`, 10_000, 'review', [app.targetPath(rel)]);
  });

  // -- delivery planning: backward-scheduled unit scripts/recording, milestones, ICS, reminders -
  const fmtMinutes = (min: number): string => {
    if (min < 60) return `${Math.round(min)} min`;
    const h = Math.floor(min / 60);
    const m = Math.round(min % 60);
    return m ? `${h}h ${m}m` : `${h}h`;
  };
  const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;

  async function planFor(module: string): Promise<ModulePlanResponse> {
    const row = await app.db.getPlan(module);
    const status = await app.status.get();
    const topics = status ? moduleTopicsFor(status, module) : [];
    const plan = computeModulePlan(topics, row.delivery_date);
    const icsUrl = `${app.auth.baseUrl}/api/module/${module}/plan/${row.ics_token}/calendar.ics`;
    return { module, deliveryDate: row.delivery_date, ownerName: row.owner_name, ownerEmail: row.owner_email, icsUrl, plan };
  }

  f.get(`/api/module/:name(${MODULE})/plan`, async (req: Req) => planFor(req.params.name));

  f.put(`/api/module/:name(${MODULE})/plan`, async (req: Req) => {
    const data = await readJson(req) as ModulePlanRequest;
    const fields: { delivery_date?: string | null; owner_name?: string; owner_email?: string } = {};
    if ('deliveryDate' in data) {
      const d = data.deliveryDate;
      if (d !== null && d !== undefined && !/^\d{4}-\d{2}-\d{2}$/.test(d)) throw new BadRequest('deliveryDate must be YYYY-MM-DD or null.');
      fields.delivery_date = d ?? null;
    }
    if ('ownerName' in data) fields.owner_name = String(data.ownerName ?? '');
    if ('ownerEmail' in data) {
      const email = String(data.ownerEmail ?? '');
      if (email && !email.includes('@')) throw new BadRequest('ownerEmail does not look like an email address.');
      fields.owner_email = email;
    }
    await app.db.upsertPlan(req.params.name, fields);
    return planFor(req.params.name);
  });

  // Not session-guarded (see the onRequest hook above) — token-guarded instead, since a
  // calendar client can't send a cookie. The token is a path segment, not a query string
  // (see the ICS_PATH comment), and a wrong or missing one looks like a 404, same as a
  // module that doesn't exist, so a guessed token can't confirm a module's name.
  f.get(`/api/module/:name(${MODULE})/plan/:token/calendar.ics`, async (req: Req, reply) => {
    const module = req.params.name;
    const row = await app.db.getPlan(module);
    const given = Buffer.from(String(req.params.token || ''));
    const expected = Buffer.from(row.ics_token);
    if (given.length !== expected.length || !timingSafeEqual(given, expected)) {
      return reply.code(404).send({ error: 'Not found.' });
    }
    const status = await app.status.get();
    const topics = status ? moduleTopicsFor(status, module) : [];
    const plan = computeModulePlan(topics, row.delivery_date);
    const events: IcsEvent[] = plan.tasks.filter((t) => t.deadline).map((t) => ({
      uid: `${module}-${t.unit}-${t.kind}@beacon-ui`,
      date: t.deadline as string,
      summary: t.kind === 'scripts' ? `Scripts ready: ${module} ${t.unit}` : `Record: ${module} ${t.unit}`,
      description: t.kind === 'scripts'
        ? `${plural(t.topics.length, 'script')} to write.`
        : `${plural(t.topics.length, 'topic')}, ~${fmtMinutes(t.estimatedMinutes)} total, in one session.`,
    }));
    if (plan.deliveryDate) events.push({ uid: `${module}-delivery@beacon-ui`, date: plan.deliveryDate, summary: `Delivery: ${module}` });
    const ics = buildIcs(module, events);
    reply.header('Content-Type', 'text/calendar; charset=utf-8');
    reply.header('Content-Disposition', `inline; filename="${module}-plan.ics"`);
    return reply.send(ics);
  });

  f.post(`/api/module/:name(${MODULE})/plan/remind`, async (req: Req): Promise<ModulePlanRemindResponse> => {
    const module = req.params.name;
    const { ownerName, ownerEmail, deliveryDate, plan } = await planFor(module);
    if (!ownerEmail) throw new BadRequest('Set an owner email before sending a reminder.');
    const upcoming = plan.tasks.filter((t) => t.deadline).slice(0, 5);
    const lines = [
      `Hi ${ownerName || 'there'},`, '',
      `Progress on ${module}: ${plan.topicsRecorded}/${plan.topicsTotal} recorded, ${plan.topicsNotDrafted} not yet drafted.`,
      deliveryDate ? `Delivery date: ${deliveryDate}.` : 'No delivery date set yet.', '',
      ...(upcoming.length ? ['Upcoming:', ...upcoming.map((t) => `- ${t.deadline}: ${t.kind === 'scripts' ? `write ${plural(t.topics.length, 'script')} for` : 'record'} ${module} ${t.unit}`)] : []),
      '', `${app.auth.baseUrl}/#/module/${module}`,
    ];
    await app.auth.mailer.send(ownerEmail, `NUCS: progress on ${module}`, lines.join('\n'));
    return { ok: true, sentTo: ownerEmail };
  });

  f.get('/api/plans', async (): Promise<PlansSummaryResponse> => {
    const rows = await app.db.allPlans();
    const status = await app.status.get();
    const out: PlansSummaryResponse = {};
    for (const row of rows) {
      const topics = status ? moduleTopicsFor(status, row.module) : [];
      const plan = computeModulePlan(topics, row.delivery_date);
      out[row.module] = { deliveryDate: row.delivery_date, onTrack: plan.onTrack, daysBehind: plan.daysBehind, milestones: plan.milestones };
    }
    return out;
  });

  // -- jobs -----------------------------------------------------------------------------------
  f.get('/api/jobs', async (req: Req) => ({ jobs: await app.jobs.list(toInt(req.query.limit ?? '100', 'limit')) }));

  f.get('/api/jobs/:id(^\\d+$)', async (req: Req, reply) => {
    const job = await app.jobs.get(Number(req.params.id));
    return job ?? reply.code(404).send({ error: 'No such job.' });
  });

  f.post('/api/jobs', async (req, reply) => {
    const data = await readJson(req);
    const command = String(data.command ?? '');
    const args: JobArgs = { ...((data.args && typeof data.args === 'object' ? data.args : {}) as JobArgs) };
    for (const k of ['from', 'import', 'by']) delete args[k];  // server-supplied only
    const targets = (Array.isArray(data.targets) ? data.targets : []).map((t) => app.targetRel(String(t)));
    const operator = await app.operator();
    if (command === 'review' || command === 'ack' || (command === 'cues' && (args.set || args.unset))) {
      args.by = operator || 'ui';
    }
    return reply.code(202).send(await app.jobs.submit(command, targets, args, jobLabel(command, targets, args), operator));
  });

  f.post('/api/jobs/:id(^\\d+$)/cancel', async (req: Req, reply) => {
    const job = await app.jobs.cancel(Number(req.params.id));
    return job ?? reply.code(404).send({ error: 'No such job, or it has already finished.' });
  });

  f.post('/api/intake', async (req, reply) => {
    const data = await readJson(req);
    const text = String(data.text ?? '');
    if (!text.trim()) throw new BadRequest('Paste some text first.');
    const d = join(app.dataDir, 'intake');
    mkdirSync(d, { recursive: true });
    const file = join(d, `${stamp()}.md`);
    writeFileSync(file, text, 'utf8');
    const target = app.targetRel(String(data.path || '.'));
    const args: JobArgs = { from: file, dry_run: Boolean(data.dry_run) };
    const job = await app.jobs.submit('intake', [target], args, `intake${args.dry_run ? ' (check only)' : ''} · paste`, await app.operator());
    return reply.code(202).send(job);
  });

  // A batch of real topic.md files from a content creator — a zip (bcn intake reads every
  // .md file inside and concatenates them, same as a multi-topic paste) rather than one
  // pasted blob. Written beside the paste files in dataDir/intake/, not the programme root:
  // like a paste, it's a transient input, not a programme artifact worth keeping around.
  f.post('/api/intake/upload', async (req: Req, reply) => {
    const name = (req.query.name ?? 'batch.zip').replace(/[^A-Za-z0-9._-]/g, '_');
    if (!name.toLowerCase().endsWith('.zip')) throw new BadRequest('Upload a .zip file.');
    const n = Number(req.headers['content-length'] || 0);
    if (!(n > 0) || n > UPLOAD_MAX) throw new BadRequest('Upload is empty or too large.');
    const d = join(app.dataDir, 'intake');
    mkdirSync(d, { recursive: true });
    const file = join(d, `${stamp()}-${name}`);
    await pipeline(req.body as IncomingMessage, createWriteStream(file));
    const target = app.targetRel(String(req.query.path || '.'));
    const args: JobArgs = { from: file, dry_run: req.query.dry_run === '1' };
    const job = await app.jobs.submit('intake', [target], args, `intake${args.dry_run ? ' (check only)' : ''} · ${name}`, await app.operator());
    return reply.code(202).send(job);
  });

  // -- translation ----------------------------------------------------------------------------
  f.get('/api/translation', async () => {
    const base = join(app.root, 'translation');
    const listing = (sub: string): TranslationItem[] => {
      const d = join(base, sub);
      if (!existsSync(d) || !statSync(d).isDirectory()) return [];
      return readdirSync(d).filter((n) => !n.startsWith('.')).sort().reverse().map((name) => {
        const p = join(d, name);
        const st = statSync(p);
        return { name, path: relative(app.root, p), kind: st.isFile() ? 'zip' : 'folder', bytes: st.isFile() ? st.size : null,
          mtime: now(st.mtime) };
      });
    };
    return { exports: listing('exports'), returned: listing('returned') };
  });

  f.post('/api/translation/upload', async (req: Req, reply) => {
    const name = (req.query.name ?? 'returned.zip').replace(/[^A-Za-z0-9._-]/g, '_');
    if (!name.toLowerCase().endsWith('.zip')) throw new BadRequest('Upload a .zip file.');
    const n = Number(req.headers['content-length'] || 0);
    if (!(n > 0) || n > UPLOAD_MAX) throw new BadRequest('Upload is empty or too large.');
    const d = join(app.root, 'translation', 'returned');
    mkdirSync(d, { recursive: true });
    let dest = join(d, name);
    if (existsSync(dest)) dest = join(d, `${basename(name, extname(name))}-${Math.floor(Date.now() / 1000)}.zip`);
    const tmp = join(d, `.${basename(dest)}.partial`);
    await pipeline(req.body as IncomingMessage, createWriteStream(tmp));
    renameSync(tmp, dest);
    return reply.code(201).send({ path: relative(app.root, dest) });
  });

  f.post('/api/translation/import', async (req, reply) => {
    const data = await readJson(req);
    const src = app.safePath(String(data.source ?? ''));
    const returned = app.safePath('translation/returned');
    if (!src.startsWith(returned + sep)) throw new Forbidden();
    const target = app.targetRel(String(data.path || '.'));
    const job = await app.jobs.submit('translation', [target], { import: src }, `translation import · ${basename(src)}`, await app.operator());
    return reply.code(202).send(job);
  });

  // -- transfer (whole-tree batch export/import, no live shared folder needed) --------------
  // Export is triggered via the generic /api/jobs endpoint, like translation's export
  // (beacon.runJob('transfer', [scope], { export: true, media, nested })) — only listing,
  // upload and import need dedicated routes, for the same reasons translation's do.
  f.get('/api/transfer', async () => {
    const d = join(app.root, 'transfer', 'exports');
    if (!existsSync(d) || !statSync(d).isDirectory()) return { exports: [] as TranslationItem[] };
    const exports: TranslationItem[] = readdirSync(d).filter((n) => !n.startsWith('.')).sort().reverse().map((name) => {
      const p = join(d, name);
      const st = statSync(p);
      return { name, path: relative(app.root, p), kind: st.isFile() ? 'zip' : 'folder', bytes: st.isFile() ? st.size : null,
        mtime: now(st.mtime) };
    });
    return { exports };
  });

  f.post('/api/transfer/upload', { bodyLimit: TRANSFER_UPLOAD_MAX }, async (req: Req, reply) => {
    const name = (req.query.name ?? 'batch.zip').replace(/[^A-Za-z0-9._-]/g, '_');
    if (!name.toLowerCase().endsWith('.zip')) throw new BadRequest('Upload a .zip file.');
    const n = Number(req.headers['content-length'] || 0);
    if (!(n > 0) || n > TRANSFER_UPLOAD_MAX) throw new BadRequest('Upload is empty or too large.');
    const d = join(app.root, 'transfer', 'incoming');
    mkdirSync(d, { recursive: true });
    let dest = join(d, name);
    if (existsSync(dest)) dest = join(d, `${basename(name, extname(name))}-${Math.floor(Date.now() / 1000)}.zip`);
    const tmp = join(d, `.${basename(dest)}.partial`);
    await pipeline(req.body as IncomingMessage, createWriteStream(tmp));
    renameSync(tmp, dest);
    return reply.code(201).send({ path: relative(app.root, dest) });
  });

  f.post('/api/transfer/import', async (req, reply) => {
    const data = await readJson(req);
    const src = app.safePath(String(data.source ?? ''));
    const incoming = app.safePath('transfer/incoming');
    if (!src.startsWith(incoming + sep)) throw new Forbidden();
    const target = app.targetRel(String(data.path || '.'));
    const args: JobArgs = { import: src };
    if (data.dry_run) args.dry_run = true;
    if (data.full) args.full = true;
    if (data.init) args.init = true;
    const job = await app.jobs.submit('transfer', [target], args, `transfer import · ${basename(src)}`, await app.operator());
    return reply.code(202).send(job);
  });

  // The editor's video and subtitles, for one topic, straight from the browser. The file is
  // named for the topic here, so the import's own naming rules always match it. It then goes
  // through bcn transfer --import like any other handover file: same scope check, same
  // refusal to overwrite, and the diff protection that a re-cut needs to opt out of with replace.
  const MEDIA_KINDS: Record<string, { suffix: string; ext: string }> = {
    master: { suffix: '', ext: 'mp4' }, en: { suffix: '', ext: 'srt' }, zh: { suffix: '.zh', ext: 'srt' },
  };
  f.post(`/api/topic/:id(${TOPIC})/media`, { bodyLimit: TRANSFER_UPLOAD_MAX }, async (req: Req, reply) => {
    const kind = MEDIA_KINDS[String(req.query.kind ?? '')];
    if (!kind) throw new BadRequest('kind must be master, en or zh.');
    const n = Number(req.headers['content-length'] || 0);
    if (!(n > 0) || n > TRANSFER_UPLOAD_MAX) throw new BadRequest('Upload is empty or too large.');
    const id = req.params.id;
    const dir = join(app.root, 'transfer', 'incoming', `media-${stamp()}`);
    mkdirSync(dir, { recursive: true });
    const file = join(dir, `${id}${kind.suffix}.${kind.ext}`);
    await pipeline(req.body as IncomingMessage, createWriteStream(file));
    const args: JobArgs = { import: file };
    if (req.query.replace === '1') args.replace = true;
    const job = await app.jobs.submit('transfer', [app.targetRel(id)], args, `media ${basename(file)} · ${id}`, await app.operator());
    return reply.code(202).send(job);
  });

  // -- backups (admin only) ------------------------------------------------------------------
  const backups = new Backups();
  const isAdmin = (req: Req) => testDisableAuth
    || ADMIN_EMAILS.has(String((req as FastifyRequest & { email?: string }).email ?? '').toLowerCase());
  const requireAdmin = (req: Req) => { if (!isAdmin(req)) throw new Forbidden('This is for administrators only.'); };

  f.get('/api/admin/backups', async (req: Req) => {
    requireAdmin(req);
    return { enabled: backups.enabled, items: await backups.list() };
  });

  // Confirms which theme is actually resolved on this server and whether it's complete --
  // load_theme() validates everything that matters (the right files exist, the CSS declares
  // the right name), so this surfaces the exact same check bumpers/render would hit.
  f.get('/api/admin/theme', async (req: Req) => {
    requireAdmin(req);
    return app.bcn.query<BcnThemecheckEnvelope>('themecheck', [app.root], 15_000);
  });

  // programme.toml as plain text, for hand-editing until each setting has its own control.
  // A save is checked by bcn itself first (themecheck loads the config exactly as every job
  // does), so a syntax error or an unknown key is refused rather than breaking every job.
  // The hash guards against overwriting a change made since the editor loaded the file.
  const programmeToml = () => join(app.root, 'programme.toml');

  f.get('/api/admin/programme-toml', async (req: Req) => {
    requireAdmin(req);
    const text = readFileSync(programmeToml(), 'utf8');
    return { text, sha256: sha256(text) };
  });

  f.put('/api/admin/programme-toml', async (req: Req) => {
    requireAdmin(req);
    const data = await readJson(req);
    const text = data.text;
    if (typeof text !== 'string') throw new BadRequest('Send the file as text.');
    if (text.length > 256 * 1024) throw new BadRequest('programme.toml is too large.');
    if (sha256(readFileSync(programmeToml(), 'utf8')) !== data.sha256) {
      throw new BadRequest('programme.toml has changed since you opened it. Reload it and make your edit again.');
    }
    mkdirSync(app.dataDir, { recursive: true });
    const check = realpathSync(mkdtempSync(join(app.dataDir, '.programme-toml-')));
    try {
      writeFileSync(join(check, 'programme.toml'), text, 'utf8');
      const env = await app.bcn.query<BcnThemecheckEnvelope>('themecheck', [check], 15_000);
      const bad = (env.diagnostics || []).filter((d) => d.code === 'CONFIG_INVALID');
      if (bad.length) throw new BadRequest(`Not saved: ${bad.map((d) => d.message).join(' ')}`);
    } finally {
      rmSync(check, { recursive: true, force: true });
    }
    // Written beside the original and renamed over it, so a job never reads half a file.
    const tmp = join(app.root, `.programme.toml.${stamp()}.partial`);
    writeFileSync(tmp, text, 'utf8');
    renameSync(tmp, programmeToml());
    return { text, sha256: sha256(text) };
  });

  f.get('/api/admin/backups/download', async (req: Req, reply) => {
    requireAdmin(req);
    const key = String(req.query.key || '');
    const { body, bytes } = await backups.open(key);
    reply.header('Content-Length', bytes);
    reply.header('Content-Disposition', `attachment; filename="${basename(key)}"`);
    return reply.type('application/octet-stream').send(body);
  });

  // Restoring a full backup merges it into the live programme, exactly like importing a zip:
  // anything that already exists with different content is reported, not overwritten.
  f.post('/api/admin/backups/restore', async (req: Req, reply) => {
    requireAdmin(req);
    const data = await readJson(req);
    const key = String(data.key ?? '');
    if (!key.includes('/full/') || !key.endsWith('.zip')) throw new BadRequest('Only a full programme backup can be restored here.');
    const d = join(app.root, 'transfer', 'incoming');
    mkdirSync(d, { recursive: true });
    const name = basename(key);
    const dest = join(d, `restore-${stamp()}-${name}`);
    await backups.download(key, dest);
    const job = await app.jobs.submit('transfer', ['.'], { import: dest, full: true }, `restore backup · ${name}`, await app.operator());
    return reply.code(202).send(job);
  });

  // -- theme (drop in a whole custom theme as one zip; no form, no per-field editing) -------
  // Admin only, like the rest of the Admin page these are used from.
  // Fixed location and name: uploading always replaces the one themes/custom/ directory.
  // bcn's own load_theme() already prefers <root>/themes/<name>/ over the bundled theme
  // baked into the image (tooling/bcn/config.py), so nothing about theme *resolution*
  // needed to change — this only adds a way to get files there without disk access, and a
  // way to flip programme.toml's theme line without hand-editing it.
  const THEME_NAME = 'custom';
  const themeDir = () => join(app.root, 'themes', THEME_NAME);

  // Finds the [programme] section of programme.toml and returns its bounds, so the theme
  // line can be read or replaced without disturbing [sync] or any other section.
  function programmeSection(toml: string): { start: number; end: number } {
    const start = toml.search(/^\[programme\]/m);
    if (start === -1) throw new BadRequest('programme.toml has no [programme] section.');
    const nextSection = toml.slice(start + 1).search(/^\[/m);
    const end = nextSection === -1 ? toml.length : start + 1 + nextSection;
    return { start, end };
  }

  function currentThemeName(toml: string): string {
    const { start, end } = programmeSection(toml);
    const m = /^\s*theme\s*=\s*"(.*?)"\s*$/m.exec(toml.slice(start, end));
    return m ? m[1] : 'default';
  }

  f.get('/api/theme', async (req: Req) => {
    requireAdmin(req);
    const d = themeDir();
    const uploaded = existsSync(d) && statSync(d).isDirectory();
    const files = uploaded ? readdirSync(d).filter((n) => !n.startsWith('.')).sort() : [];
    const toml = readFileSync(join(app.root, 'programme.toml'), 'utf8');
    const active = currentThemeName(toml) === THEME_NAME;
    return { uploaded, active, files };
  });

  f.post('/api/theme/upload', async (req: Req, reply) => {
    requireAdmin(req);
    const n = Number(req.headers['content-length'] || 0);
    if (!(n > 0) || n > UPLOAD_MAX) throw new BadRequest('Upload is empty or too large.');
    // The temp zip and extract dir must be staged under app.root, not app.dataDir: the two
    // can be separate Docker volumes (separate devices), and the final rename into
    // themes/custom/ has to be an atomic same-device rename, not a cross-device copy.
    const themesDir = join(app.root, 'themes');
    mkdirSync(themesDir, { recursive: true });
    const zipPath = join(themesDir, `.upload-${stamp()}.zip`);
    await pipeline(req.body as IncomingMessage, createWriteStream(zipPath));
    const extractTo = join(themesDir, `.extract-${stamp()}`);
    try {
      try {
        await extractZip(zipPath, { dir: extractTo });
      } catch {
        throw new BadRequest('Not a valid zip file.');
      }
      const d = themeDir();
      if (existsSync(d)) rmSync(d, { recursive: true, force: true });
      renameSync(extractTo, d);
      const files = readdirSync(d).filter((n) => !n.startsWith('.')).sort();
      return reply.code(201).send({ files });
    } finally {
      try { unlinkSync(zipPath); } catch { /* fine */ }
      try { rmSync(extractTo, { recursive: true, force: true }); } catch { /* already moved, or never created */ }
    }
  });

  f.post('/api/theme/activate', async (req: Req, reply) => {
    requireAdmin(req);
    const data = await readJson(req);
    const active = Boolean(data.active);
    if (active && !existsSync(themeDir())) throw new BadRequest('Upload a theme before activating it.');
    const tomlPath = join(app.root, 'programme.toml');
    const toml = readFileSync(tomlPath, 'utf8');
    const name = active ? THEME_NAME : 'default';
    const lineRe = /^(\s*theme\s*=\s*)".*?"(\s*)$/m;
    const { start, end } = programmeSection(toml);
    const section = toml.slice(start, end);
    const updated = lineRe.test(section) ? section.replace(lineRe, `$1"${name}"$2`) : section.replace(/\n?$/, `\ntheme = "${name}"\n`);
    writeFileSync(tomlPath, toml.slice(0, start) + updated + toml.slice(end), 'utf8');
    return reply.send({ active });
  });

  // -- prefs ----------------------------------------------------------------------------------
  f.get('/api/prefs', async () => app.db.prefs());

  f.put('/api/prefs', async (req) => {
    const data = await readJson(req);
    if ('jobs' in data) data.jobs = clamp(toInt(data.jobs, 'jobs'), 1, 16);
    if ('parallel_jobs' in data) data.parallel_jobs = clamp(toInt(data.parallel_jobs, 'parallel_jobs'), 1, 8);
    if ('poll_seconds' in data) data.poll_seconds = clamp(toInt(data.poll_seconds, 'poll_seconds'), 5, 600);
    return await app.db.setPrefs(data);
  });

  // -- live events ----------------------------------------------------------------------------
  f.get('/api/events', (req, reply) => {
    reply.hijack();
    const res = reply.raw;
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
    res.write(`event: hello\ndata: ${pyJson({ status_version: app.status.version })}\n\n`);
    const off = app.bus.subscribe((ev) => {
      if (ev.type === 'job-wake') return;  // internal, for workers only: never sent to the browser
      if (res.writableLength > SSE_BACKLOG) { res.destroy(); return false; }
      res.write(`data: ${JSON.stringify(ev)}\n\n`);
    });
    const ping = setInterval(() => res.write(': ping\n\n'), PING_MS);
    streams.add(res);
    req.raw.on('close', () => { clearInterval(ping); off(); streams.delete(res); });
  });

  // -- files from the root --------------------------------------------------------------------
  f.get('/files/*', async (req: Req, reply) => {
    const rawPath = (req.raw.url || '').split('?')[0];
    const p = app.safePath(rawPath.slice('/files/'.length));
    if (!existsSync(p) || !statSync(p).isFile()) return reply.code(404).send({ error: 'No such file.' });
    const q = req.query;
    const ext = extname(p).toLowerCase();
    if (ext === '.srt' && q.format === 'vtt') {
      const text = (await readFile(p)).toString('utf8').replace(/^﻿/, '').replace(/\r\n/g, '\n');
      const vtt = 'WEBVTT\n\n' + text.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, '$1.$2');
      return reply.type('text/vtt; charset=utf-8').send(vtt);
    }
    if (q.view && ['.md', '.txt', '.json', '.toml', '.csv', '.srt', '.vtt'].includes(ext)) {
      // For the in-app document viewer: always text, never a download.
      return reply.type('text/plain; charset=utf-8').send(await readFile(p));
    }
    const size = statSync(p).size;
    reply.header('Accept-Ranges', 'bytes');
    reply.header('Content-Type', mimeType(p));
    if (q.download) {
      // The browser's own `download` attribute on an <a> is only a fallback name: once the
      // response carries a Content-Disposition filename, that wins. So a caller wanting a
      // nicer name than the file's own (e.g. the module code instead of "course-map.pdf")
      // passes it here (?download=KV7016-map.pdf) rather than relying on the anchor alone.
      // `download=1` keeps the old behaviour of just using the file's real name.
      const name = q.download === '1' ? basename(p) : String(q.download).replace(/[\r\n"]/g, '');
      reply.header('Content-Disposition', `attachment; filename="${name}"`);
    }
    const m = /^bytes=(\d*)-(\d*)$/.exec(String(req.headers.range || ''));
    if (m && (m[1] || m[2])) {
      let start: number, end: number;
      if (m[1]) {
        start = Number(m[1]);
        end = m[2] ? Number(m[2]) : size - 1;
      } else {
        start = Math.max(0, size - Number(m[2]));
        end = size - 1;
      }
      end = Math.min(end, size - 1);
      if (start > end || start >= size) {
        return reply.code(416).header('Content-Range', `bytes */${size}`).type('text/plain').send('');
      }
      reply.code(206).header('Content-Range', `bytes ${start}-${end}/${size}`).header('Content-Length', String(end - start + 1));
      return reply.send(createReadStream(p, { start, end }));
    }
    reply.header('Content-Length', String(size));
    return reply.send(createReadStream(p));
  });

  // -- the app --------------------------------------------------------------------------------
  const root = staticDir ? resolve(staticDir) : null;
  const serveApp = async (req: FastifyRequest, reply: FastifyReply) => {
    const path = (req.raw.url || '/').split('?')[0];
    if (path.startsWith('/api/')) return reply.code(404).send({ error: 'Not found.' });
    if (!root || !existsSync(join(root, 'index.html'))) {
      return reply.type('text/html; charset=utf-8').send(
        '<!doctype html><title>NUCS</title><p>The NUCS app has not been built yet. Run <code>scripts/setup.sh</code>, or <code>npm run build</code> in <code>ui/</code>.</p>');
    }
    let rel = path.replace(/^\/+/, '');
    try { rel = decodeURIComponent(rel); } catch { /* use as is */ }
    let p = resolve(root, rel || 'index.html');
    if (!p.startsWith(root + sep) || !existsSync(p) || !statSync(p).isFile()) p = join(root, 'index.html');  // client-side routes
    // Built assets have hashed names and never change; the page itself must always be fresh.
    if (p.includes(`${sep}assets${sep}`)) reply.header('Cache-Control', 'public, max-age=31536000, immutable');
    return reply.type(withCharset(mimeType(p))).send(await readFile(p));
  };
  f.get('/', serveApp);
  f.get('/*', serveApp);

  return f;
}

