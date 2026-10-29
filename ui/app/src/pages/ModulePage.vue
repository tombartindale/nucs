<script setup lang="ts">
// Module: units down, topics across, one split cell per topic (English | Mandarin).
// Stage is shown by position; colour marks only stale and blocked. Bulk actions live here,
// where the scope is visible, and say what they will do before they run.
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import type { LangState, ModulePlanResponse, StepState, TopicStatus } from '@beacon/shared';
import PageHeader from '@/components/PageHeader.vue';
import StagePips from '@/components/StagePips.vue';
import StateChip from '@/components/StateChip.vue';
import { api, fileUrl } from '@/api';
import { confirm } from '@/composables/confirm';
import { fmtAgo, fmtMinutes, LANG_NAME, plural, STAGE_LABEL, STEP_HELP, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

const BULK = ['validate', 'render', 'script', 'bumpers', 'cues', 'compose', 'package'];
const props = defineProps<{ module: string }>();
const route = useRoute();
const beacon = useBeacon();

const state = reactive({
  selected: new Set<string>(),
  lang: 'en' as 'en' | 'zh',    // language for bulk actions
  force: false,
  filter: { wait: '', stale: false, blocked: false },
});

const env = computed(() => beacon.status);
const rows = computed(() => (env.value?.results || []).filter((r) => r.module === props.module));
const m = computed(() => env.value?.summary.modules[props.module]);
const units = computed(() => [...new Set(rows.value.map((r) => r.unit))].sort());
const codes = computed(() => [...new Set(rows.value.map((r) => r.code))].sort());
const grid = computed(() => units.value.map((unit) => ({
  unit, topics: rows.value.filter((x) => x.unit === unit).sort((a, b) => a.code.localeCompare(b.code)),
  activity: (m.value?.documents || []).find((d) => d.path === `${props.module}/${unit}/activity.md`) ?? null,
})));
const cols = computed(() => beacon.boot?.prefs.module_columns || { en: true, zh: true });
// Translated languages only: English is the source, shown as the three "awaiting" columns.
const langs = computed(() => (['zh'] as const).filter((l) => cols.value[l] !== false));

// What we wait for from outside: the draft script, the recording and its subtitles (English
// source files), then per language whether the translated copy has come back.
const AWAITING = [
  { key: 'script', label: 'Script', file: 'topic.md', help: 'Draft script (topic.md)' },
  { key: 'recording', label: 'Recording', file: 'edit/master.mp4', help: 'Recording (edit/master.mp4)' },
  { key: 'subtitles', label: 'Subtitles', file: 'edit/master.srt', help: 'Subtitles (edit/master.srt)' },
] as const;
type AwaitKey = typeof AWAITING[number]['key'];
function have(r: TopicStatus, key: AwaitKey) {
  const file = AWAITING.find((a) => a.key === key)!.file;
  if (key === 'script' && r.en.stage === 'planned') return false;
  return r.artifacts.some((a) => a.key === file && a.exists);
}
// The recording session is ticked by hand (bcn session, kept in review.json) so we can see
// where filming is up to before the editor sends anything. A recording on disk implies it.
const sessionDone = (r: TopicStatus) => !!r.session || have(r, 'recording');
const sessionsDone = computed(() => rows.value.filter(sessionDone).length);
function toggleSession(r: TopicStatus, on: boolean) {
  beacon.runJob('session', [r.path], on ? {} : { clear: true });
}
// Translated copy is "in the system" once the stage reaches 'returned' (script + subtitles on disk).
function translated(r: TopicStatus, lang: 'en' | 'zh') {
  const l = r[lang], at = l.stages.indexOf('returned');
  return at >= 0 && l.stage_index >= at;
}

// The default-layout compose output to show on the module grid: whichever file the step's
// own last run actually wrote (steps.compose.draft, same flag TopicActions.vue reads — a
// draft run writes build/draft.mp4, a delivered one writes build/composed.mp4; they're
// separate files, not one file with a mode flag, so which artifact to point the player at
// follows from which run was most recent, not from which files happen to exist).
function composeOutput(r: TopicStatus, lang: 'en' | 'zh') {
  const st = r[lang].steps.compose;
  if (!st?.exists) return null;
  const kind = st.draft ? 'draft' : 'composed';
  const art = r.artifacts.find((a) => a.kind === kind && a.lang === lang && a.exists);
  if (!art) return null;
  return { path: art.path, mtime: art.mtime, draft: !!st.draft, stale: st.fresh === false };
}
const videoDialog = reactive({ open: false, title: '', src: '', draft: false });
function openComposeVideo(r: TopicStatus, lang: 'en' | 'zh') {
  const out = composeOutput(r, lang);
  if (!out) return;
  videoDialog.src = fileUrl(out.path, out.mtime);
  videoDialog.title = `${r.code} · ${LANG_NAME[lang]}${out.draft ? ' · draft' : ''}`;
  videoDialog.draft = out.draft;
  videoDialog.open = true;
}
function issue(l: LangState): 'error' | 'warn' | null {
  return l.blocked || l.diagnostics.error ? 'error' : l.diagnostics.warn ? 'warn' : null;
}
// The draft script's own findings: validate's, not those of cues, subtitles or compose.
function scriptIssue(l: LangState): 'error' | 'warn' | null {
  const c = l.steps.validate?.counts;
  return !c ? null : c.error || l.blockers.some((b) => b.step === 'validate') ? 'error' : c.warn ? 'warn' : null;
}
const scriptTip = (l: LangState) => { const c = l.steps.validate?.counts; return [c?.error ? plural(c.error, 'error') : '', c?.warn ? plural(c.warn, 'warning') : ''].filter(Boolean).join('\n'); };
const issueTip = (l: LangState) => [...l.blockers.map((b) => b.message),
  l.diagnostics.error ? plural(l.diagnostics.error, 'error') : '', l.diagnostics.warn ? plural(l.diagnostics.warn, 'warning') : ''].filter(Boolean).join('\n');
const waitingOn = computed(() => Object.fromEntries(AWAITING.map((a) => [a.key, rows.value.filter((r) => !have(r, a.key)).length])) as Record<AwaitKey, number>);
const waitingTrans = computed(() => Object.fromEntries(langs.value.map((l) => [l, rows.value.filter((r) => !translated(r, l)).length])) as Record<string, number>);
// Pipeline steps between the awaited inputs and delivery. The compact view collapses them to
// one icon that only appears when a step has failed or gone stale; the detailed view shows each.
const DETAIL_KEY = 'beacon.moduleDetail';
const detail = ref((() => { try { return localStorage.getItem(DETAIL_KEY) === '1'; } catch { return false; } })());
watch(detail, (v) => { try { localStorage.setItem(DETAIL_KEY, v ? '1' : '0'); } catch { /* private window: fine */ } });
const stepKeys = (lang: 'en' | 'zh') => [...new Set(rows.value.flatMap((r) => Object.keys(r[lang].steps)))];
const STEP_ICON = {
  ok: ['check', 'positive'], failed: ['error', 'negative'], stale: ['update', 'warning'], draft: ['edit_note', 'warning'], none: ['remove', 'grey-5'],
} as const;
// A step that refused because something earlier is missing has ok=false but is only waiting
// (the server leaves it out of the blockers), so "failed" means a real finding from that step.
function stepStatus(s: StepState | undefined, step?: string, l?: LangState): keyof typeof STEP_ICON {
  if (!s || !s.exists) return 'none';
  if (s.ok === false) return l && step && l.blockers.some((b) => b.step === step) ? 'failed' : 'none';
  if (s.fresh === false) return 'stale';
  if (s.draft) return 'draft';
  return 'ok';
}
const STEP_WORD = { ok: 'up to date', failed: 'failed', stale: 'out of date (inputs changed since it ran)', draft: 'draft only, not delivered', none: "not run yet, or waiting on an earlier step" };
const stepTip = (name: string, s: StepState | undefined, step?: string, l?: LangState) =>
  `${name}: ${STEP_WORD[stepStatus(s, step, l)]}${s?.time ? `\nran ${fmtAgo(s.time)}` : ''}`;
// Bumpers isn't a pipeline step on the backend (no StepState, same as TopicActions.vue's own
// 'bumpers' kind) — it's a tool derived from the bumper_card/bumper artifacts themselves, so it
// never appears in stepKeys(). Spliced into the detail column list right before 'compose'
// (which draws on it), the one place it actually sits in the pipeline.
function bumperStatus(r: TopicStatus, lang: 'en' | 'zh'): 'ok' | 'stale' | 'none' {
  const made = r.artifacts.filter((a) => a.kind.startsWith('bumper') && a.lang === lang && a.exists);
  if (!made.length) return 'none';
  return made.some((a) => a.stale) ? 'stale' : 'ok';
}
const BUMPER_WORD = { ok: 'made', stale: 'out of date (newer script than the bumpers)', none: 'not made yet (optional; compose can also run with --no-bumpers)' };
const bumperTip = (name: string, r: TopicStatus, lang: 'en' | 'zh') => `${name}: ${BUMPER_WORD[bumperStatus(r, lang)]}`;
function detailCols(lang: 'en' | 'zh') {
  const keys = stepKeys(lang);
  const out = [...keys];
  const i = out.indexOf('compose');
  out.splice(i === -1 ? out.length : i, 0, 'bumpers');
  return out;
}
// Steps with a problem (failed or stale), for the compact view's single icon.
function stepIssues(l: LangState) {
  return Object.entries(l.steps).map(([k, s]) => [k, stepStatus(s, k, l)] as const).filter(([, st]) => st === 'failed' || st === 'stale');
}
const pipelineTip = (r: TopicStatus) => [['en', r.en] as const, ...langs.value.map((l) => [l, r[l]] as const)]
  .flatMap(([l, st]) => stepIssues(st).map(([k, v]) => `${l.toUpperCase()} ${k}: ${v === 'failed' ? 'failed' : 'out of date'}`)).join('\n');
const pipelineSeverity = (r: TopicStatus) => [r.en, ...langs.value.map((l) => r[l])]
  .flatMap(stepIssues).some(([, v]) => v === 'failed') ? 'failed' : pipelineTip(r) ? 'stale' : null;
// Breadcrumbs from a topic link here with ?unit=U01: scroll that unit's heading into view.
const focusUnit = ref('');
watch([() => route.query.unit, () => units.value.length], async ([u]) => {
  if (typeof u !== 'string' || !units.value.includes(u)) return;
  await nextTick();
  focusUnit.value = u;
  // After the router's own scroll reset and the first paint of the table.
  setTimeout(() => document.querySelector(`[data-unit="${u}"]`)?.scrollIntoView({ block: 'start' }), 150);
}, { immediate: true });
const unitPlan = (unit: string) => plan.value?.plan.units.find((u) => u.unit === unit);
const waitOptions = computed(() => [{ label: 'anything', value: '' },
  ...AWAITING.map((a) => ({ label: `waiting on ${a.label.toLowerCase()}`, value: a.key })),
  { label: 'not yet filmed', value: 'session' },
  ...langs.value.map((l) => ({ label: `waiting on ${LANG_NAME[l]}`, value: l }))]);

function matches(r: TopicStatus) {
  const f = state.filter, sides = [r.en, ...langs.value.map((l) => r[l])];
  if (f.wait === 'session') { if (sessionDone(r)) return false; }
  else if (f.wait && (f.wait in waitingOn.value ? have(r, f.wait as AwaitKey) : translated(r, f.wait as 'zh'))) return false;
  if (f.stale && !sides.some((s) => s.stale)) return false;
  if (f.blocked && !sides.some((s) => s.blocked)) return false;
  return true;
}
const visible = computed(() => rows.value.filter(matches));
const nSelected = computed(() => [...state.selected].filter((id) => rows.value.some((r) => r.topic === id)).length);

function toggle(id: string, on: boolean) {
  if (on) state.selected.add(id); else state.selected.delete(id);
}
function cellClick(e: MouseEvent, r: TopicStatus) {
  if (e.metaKey || e.shiftKey || e.ctrlKey) { e.preventDefault(); toggle(r.topic, !state.selected.has(r.topic)); }
}
const isCloud = (r: TopicStatus) => r.hydration === 'cloud' || r.hydration === 'partial';
const halfTip = (s: LangState, lang: string) => [`${lang.toUpperCase()}: ${STAGE_LABEL[s.stage]}`,
  s.stale ? `stale (${s.stale_steps.join(', ')})` : '', s.blocked ? `blocked: ${s.blockers.map((b) => b.code).join(', ')}` : '',
  s.next ? `next: ${s.next}` : ''].filter(Boolean).join('\n');

// Collapse a selection to the widest directories it fully covers, so bcn can run them with --jobs.
function targetsFor(ids: Set<string>) {
  const onDisk = rows.value.filter((r) => r.has_dir);
  const byUnit = new Map<string, string[]>();
  for (const r of onDisk) { if (!byUnit.has(r.unit)) byUnit.set(r.unit, []); byUnit.get(r.unit)!.push(r.topic); }
  const all = [...byUnit.entries()];
  if (all.every(([, ts]) => ts.every((t) => ids.has(t))) && ids.size === onDisk.length) return [props.module];
  const out: string[] = [];
  for (const [u, ts] of all) {
    const picked = ts.filter((t) => ids.has(t));
    if (!picked.length) continue;
    if (picked.length === ts.length) out.push(`${props.module}/${u}`);
    else out.push(...picked.map(topicPath));
  }
  return out;
}

async function bulk(command: string) {
  const ids = new Set([...state.selected].filter((id) => rows.value.find((r) => r.topic === id && r.has_dir)));
  if (!ids.size) return;
  const targets = targetsFor(ids);
  const lang = ['cues', 'script'].includes(command) ? 'en' : state.lang;  // English only
  const force = state.force;
  const cli = `bcn ${command}${lang === 'zh' ? ' --lang zh' : ''}${force ? ' --force' : ''}`;
  const ok = await confirm({
    title: `Run ${command} on ${plural(ids.size, 'topic')}?`,
    lines: [
      { text: `This runs ${cli} on ${plural(ids.size, 'topic')} (${command === 'cues' ? 'English; Mandarin inherits cues' : LANG_NAME[state.lang]}), as ${plural(targets.length, 'invocation')}:`, strong: cli },
      { text: targets.join('  '), mono: true },
      force ? 'Force rebuilds topics whose outputs are already current.' : 'Topics whose outputs are already current are skipped.',
    ],
  });
  if (ok) await beacon.runJob(command, targets, force ? { lang, force: true } : { lang });
}

const quizzes = computed(() => (m.value?.documents || []).filter((d) => d.quiz));
async function exportQuizzes() {
  const n = quizzes.value.length;
  const ok = await confirm({
    title: `Export ${plural(n, 'quiz', 'quizzes')} for the LMS?`,
    lines: [
      { text: `This runs bcn qti ${props.module}, writing one QTI 2.1 package per unit quiz to ${props.module}/build/qti/.`, strong: `bcn qti ${props.module}` },
      'Quizzes whose package is already current are skipped. A quiz with errors gets no package.',
    ],
  });
  if (ok) await beacon.runJob('qti', [props.module], {});
}

async function exportModuleMapPdf() {
  const job = await beacon.runJob('coursemap', [props.module], {});
  const result = await beacon.awaitJob(job.id);
  if (result.state === 'done') beacon.toast('Module map PDF ready.');
  else beacon.toast('The module map PDF could not be made; see Jobs.', true);
}

async function exportReadingList() {
  const job = await beacon.runJob('readinglist', [props.module], {});
  const result = await beacon.awaitJob(job.id);
  if (result.state === 'done') beacon.toast('Reading list ready.');
  else beacon.toast('The reading list could not be made; see Jobs.', true);
}

// Assignment briefs and unit activities (quiz or task) — everything bcn docpdf prints,
// i.e. every document except course-map.md/assets.md/reading-list.md.
const assessments = computed(() => (m.value?.documents || [])
  .filter((d) => /\/(assignment-\d+|activity)(?:\.zh)?\.md$/.test(d.path)));
async function exportAssessmentPdfs() {
  const n = assessments.value.length;
  const ok = await confirm({
    title: `Make PDFs for ${plural(n, 'assessment document')}?`,
    lines: [
      { text: `This runs bcn docpdf ${props.module}, writing one PDF per assignment brief and unit activity to ${props.module}/build/.`, strong: `bcn docpdf ${props.module}` },
      'Documents whose PDF is already current are skipped.',
    ],
  });
  if (ok) await beacon.runJob('docpdf', [props.module], {});
}

// The only module-wide document that's actually in use today; assets.md is listed by
// status but unused. reading-list.md is no longer hand-authored either — it's generated
// from the module map's own per-unit Reading paragraphs (bcn readinglist) rather than
// being a separate document someone writes.
const moduleMap = computed(() => (m.value?.documents || []).find((d) => d.path.endsWith('/course-map.md')));

// Delivery planning: fetched directly, not via the polled status store — it only changes
// on a manual edit (set delivery date/owner) and doesn't need SSE-driven refresh.
const MILESTONE_LABEL: Record<string, string> = {
  briefs_done: 'Briefs done', recorded: 'Recorded', translated: 'Translated', packaged: 'Packaged',
};
const plan = ref<ModulePlanResponse | null>(null);
const planForm = reactive({ ownerName: '', ownerEmail: '' });
const planSaving = ref(false);
const reminding = ref(false);
// One date input per unit, keyed by unit code — each unit's delivery date is agreed and
// saved independently of every other unit's (see planning.ts).
const unitDateForm = reactive<Record<string, string>>({});
const savingUnit = ref<string | null>(null);

function syncPlanForm() {
  if (!plan.value) return;
  planForm.ownerName = plan.value.ownerName;
  planForm.ownerEmail = plan.value.ownerEmail;
  for (const key of Object.keys(unitDateForm)) delete unitDateForm[key];
  for (const u of plan.value.plan.units) unitDateForm[u.unit] = u.deliveryDate ?? '';
}
async function loadPlan() {
  plan.value = await api<ModulePlanResponse>(`/api/module/${props.module}/plan`);
  syncPlanForm();
}
onMounted(loadPlan);
watch(() => props.module, loadPlan);

async function savePlan() {
  planSaving.value = true;
  try {
    plan.value = await api<ModulePlanResponse>(`/api/module/${props.module}/plan`, {
      method: 'PUT',
      body: { ownerName: planForm.ownerName, ownerEmail: planForm.ownerEmail },
    });
    beacon.toast('Delivery plan saved.');
  } catch (e) {
    beacon.toast((e as Error).message, true);
  } finally {
    planSaving.value = false;
  }
}

async function saveUnitDate(unit: string) {
  savingUnit.value = unit;
  try {
    plan.value = await api<ModulePlanResponse>(`/api/module/${props.module}/plan/units/${unit}`, {
      method: 'PUT',
      body: { deliveryDate: unitDateForm[unit] || null },
    });
    syncPlanForm();
    beacon.toast(`${unit} delivery date saved.`);
  } catch (e) {
    beacon.toast((e as Error).message, true);
  } finally {
    savingUnit.value = null;
  }
}

// The browser's own calendar-picker glyph can't be reliably sized or vertically centred
// to match a Quasar append icon (it's rendered deep in the UA's own shadow DOM, not ours
// to lay out) — so it's hidden via CSS below, and this opens the same native picker from
// a Quasar-rendered prepend icon instead, which Quasar lays out identically to the save
// button on the other side.
function openDatePicker(ev: MouseEvent) {
  const input = (ev.currentTarget as HTMLElement).closest('.q-field')?.querySelector('input[type="date"]') as HTMLInputElement | null;
  input?.showPicker?.();
}

const webcalUrl = computed(() => plan.value ? plan.value.icsUrl.replace(/^https?:\/\//, 'webcal://') : '');
async function copyIcsLink(url: string) {
  try {
    await navigator.clipboard.writeText(url);
    beacon.toast('Calendar link copied.');
  } catch {
    beacon.toast('Could not copy to clipboard.', true);
  }
}

async function sendReminder() {
  reminding.value = true;
  try {
    const res = await api<{ ok: true; sentTo: string }>(`/api/module/${props.module}/plan/remind`, { method: 'POST' });
    beacon.toast(`Reminder sent to ${res.sentTo}.`);
  } catch (e) {
    beacon.toast((e as Error).message, true);
  } finally {
    reminding.value = false;
  }
}
</script>

<template>
  <q-page padding class="page-max">
    <div v-if="!env" class="text-grey-7 q-pa-lg">Reading the programme…</div>
    <div v-else-if="!m" class="text-grey-7 q-pa-lg">No module {{ module }}.</div>
    <template v-else>
      <PageHeader stacked :title="`${module}${m.title ? ' · ' + m.title : ''}`" :crumbs="[{ label: 'Programme', to: '/' }, { label: module }]"
        :sub="`${m.topics} topics · waiting on ${waitingOn.script} scripts · ${waitingOn.recording} recordings · ${waitingOn.subtitles} subtitles`">
        <StateChip v-if="m.blocked" kind="blocked" :label="`${m.blocked} blocked`" />
        <StateChip v-if="m.stale" kind="stale" :label="`${m.stale} stale`" />
        <StateChip v-if="m.cloud" kind="cloud" :label="`☁ ${m.cloud} cloud-only`" />
        <q-btn v-if="quizzes.length" outline dense no-caps icon="quiz" label="Export quizzes to LMS" @click="exportQuizzes">
          <q-tooltip max-width="320px">{{ STEP_HELP.qti }}</q-tooltip>
        </q-btn>
        <q-btn v-if="assessments.length" outline dense no-caps icon="picture_as_pdf" label="Export assessment PDFs" @click="exportAssessmentPdfs">
          <q-tooltip max-width="320px">{{ STEP_HELP.docpdf }}</q-tooltip>
        </q-btn>
        <q-btn outline dense no-caps label="Run QA on module" @click="beacon.runJob('qa', [module], {})" />
        <template v-if="moduleMap">
          <q-btn-dropdown v-if="moduleMap.exists" split outline dense no-caps label="Module map" :to="`/edit-doc/${moduleMap.path}`">
            <q-badge v-if="moduleMap.errors" color="negative" floating>{{ moduleMap.errors }}</q-badge>
            <q-badge v-else-if="moduleMap.warnings" color="warning" floating>{{ moduleMap.warnings }}</q-badge>
            <q-list>
              <q-item clickable v-close-popup :to="`/doc/${moduleMap.path}`">
                <q-item-section avatar><q-icon name="visibility" /></q-item-section>
                <q-item-section>View</q-item-section>
              </q-item>
              <q-item clickable v-close-popup :href="fileUrl(moduleMap.path, null, `download=${module}-course-map.md`)">
                <q-item-section avatar><q-icon name="download" color="primary" /></q-item-section>
                <q-item-section>Download</q-item-section>
              </q-item>
              <q-item v-if="moduleMap.pdf?.exists" clickable v-close-popup :href="fileUrl(moduleMap.pdf.path, null, `download=${module}-map.pdf`)">
                <q-item-section avatar><q-icon name="download" :color="moduleMap.pdf.stale ? 'warning' : 'primary'" /></q-item-section>
                <q-item-section>{{ moduleMap.pdf.stale ? 'Download PDF (out of date)' : 'Download PDF' }}</q-item-section>
              </q-item>
              <q-item v-if="moduleMap.pdf && (!moduleMap.pdf.exists || moduleMap.pdf.stale)" clickable v-close-popup @click="exportModuleMapPdf">
                <q-item-section avatar><q-icon name="picture_as_pdf" /></q-item-section>
                <q-item-section>{{ moduleMap.pdf.exists ? 'Update PDF' : 'Export PDF' }}</q-item-section>
              </q-item>
            </q-list>
          </q-btn-dropdown>
          <q-btn v-else flat dense no-caps disable label="Module map — missing">
            <q-tooltip>Not in the working copy yet</q-tooltip>
          </q-btn>
          <q-btn-dropdown v-if="m.reading_list?.exists" split outline dense no-caps icon="menu_book"
            :color="m.reading_list.stale ? 'warning' : undefined" :label="m.reading_list.stale ? 'Reading list (out of date)' : 'Reading list'"
            :href="fileUrl(m.reading_list.path, null, 'download=1')">
            <q-tooltip max-width="320px">Every unit's Reading paragraph from the module map, pulled into one list.</q-tooltip>
            <q-list>
              <q-item clickable v-close-popup @click="exportReadingList">
                <q-item-section avatar><q-icon name="menu_book" /></q-item-section>
                <q-item-section>Update reading list</q-item-section>
              </q-item>
            </q-list>
          </q-btn-dropdown>
          <q-btn v-else-if="m.reading_list" outline dense no-caps icon="menu_book" label="Build reading list" @click="exportReadingList">
            <q-tooltip max-width="320px">Every unit's Reading paragraph from the module map, pulled into one list.</q-tooltip>
          </q-btn>
        </template>
      </PageHeader>

      <q-expansion-item v-if="plan" icon="event" label="Delivery planning" class="q-mb-md planning-card"
        :header-class="plan.plan.onTrack === false ? 'text-negative' : undefined">
        <template #header>
          <q-item-section avatar><q-icon name="event" /></q-item-section>
          <q-item-section>
            <q-item-label>Delivery planning</q-item-label>
            <q-item-label caption>
              {{ plan.plan.units.some((u) => u.deliveryDate) ? `${plan.plan.units.filter((u) => u.deliveryDate).length} of ${plan.plan.units.length} units dated` : 'No delivery dates agreed yet' }}
              <span v-if="plan.ownerName || plan.ownerEmail"> · {{ plan.ownerName }}{{ plan.ownerName && plan.ownerEmail ? ' · ' : '' }}{{ plan.ownerEmail }}</span>
            </q-item-label>
          </q-item-section>
          <q-item-section v-if="plan.plan.onTrack !== null" side>
            <StateChip :kind="plan.plan.onTrack ? 'ok' : 'blocked'"
              :label="plan.plan.onTrack ? 'on track' : `${plural(plan.plan.daysBehind, 'day')} behind`" />
          </q-item-section>
        </template>
        <q-card flat bordered>
          <!-- Each unit's delivery date is agreed independently — production runs one unit
               at a time, so there's no single module-wide date to infer the rest from. -->
          <q-card-section>
            <div class="row q-col-gutter-md">
              <div v-for="u in plan.plan.units" :key="u.unit" class="col-12 col-sm-6 col-md-4">
                <q-input v-model="unitDateForm[u.unit]" type="date" dense outlined class="unit-date-input"
                  :label="`${u.unit}${m.unit_titles?.[u.unit] ? ' — ' + m.unit_titles[u.unit] : ''}`">
                  <template v-slot:prepend>
                    <q-icon name="event" size="20px" class="cursor-pointer" @click="openDatePicker" />
                  </template>
                  <template v-slot:append>
                    <q-btn flat dense round size="sm" icon="save" color="primary"
                      :loading="savingUnit === u.unit" @click="saveUnitDate(u.unit)">
                      <q-tooltip>Save delivery date</q-tooltip>
                    </q-btn>
                  </template>
                </q-input>
                <div class="text-caption q-mt-xs" style="min-height: 18px"
                  :class="u.onTrack === false ? 'text-negative' : 'text-positive'">
                  {{ u.onTrack === null ? '' : (u.onTrack ? 'On track' : `${plural(u.daysBehind, 'day')} behind`) }}
                </div>
              </div>
            </div>
          </q-card-section>
          <q-separator />
          <q-card-section class="row items-end gap-md">
            <q-input v-model="planForm.ownerName" dense outlined label="Responsible — name" style="max-width: 220px" />
            <q-input v-model="planForm.ownerEmail" dense outlined label="Responsible — email" style="max-width: 260px" />
            <q-btn color="primary" no-caps label="Save" :loading="planSaving" @click="savePlan" />
          </q-card-section>
          <q-separator />

          <!-- Milestone strip: the producer's headline view, four module-wide checkpoints. -->
          <q-card-section>
            <div class="row q-col-gutter-md">
              <div v-for="ms in plan.plan.milestones" :key="ms.kind" class="col-6 col-md-3">
                <q-card flat bordered :class="{ 'bg-green-1': ms.done }">
                  <q-card-section class="q-pa-sm text-center">
                    <div class="text-caption text-grey-7">{{ MILESTONE_LABEL[ms.kind] }}</div>
                    <div class="text-h6">{{ ms.done ? '✓' : ms.remaining }}</div>
                    <div class="text-caption text-grey-7">{{ ms.done ? 'done' : plural(ms.remaining, 'topic') + ' left' }}</div>
                    <div v-if="ms.dueDate" class="text-caption">due {{ ms.dueDate }}</div>
                  </q-card-section>
                </q-card>
              </div>
            </div>
          </q-card-section>
          <q-separator />

          <!-- Task table: the content creator's detail view. Production runs one unit at a
               time — every topic's script in a unit must be ready before that unit's single
               recording session, where every topic in it is recorded together — so each row
               is a whole unit's batch of outstanding scripts or its one recording session,
               not a per-topic task. -->
          <q-card-section v-if="plan.plan.tasks.length">
            <table class="plan-tasks">
              <thead><tr><th>Unit</th><th>Task</th><th>Topics</th><th>Est.</th><th>Deadline</th></tr></thead>
              <tbody>
                <tr v-for="t in plan.plan.tasks" :key="`${t.unit}-${t.kind}`">
                  <td>{{ t.unit }}<span v-if="m.unit_titles?.[t.unit]"> — {{ m.unit_titles[t.unit] }}</span></td>
                  <td>{{ t.kind === 'scripts' ? 'Write scripts' : 'Record (one session)' }}</td>
                  <td>
                    <template v-for="(code, i) in t.topics" :key="code">
                      <span v-if="i > 0">, </span><a :href="`#/topic/${code}`">{{ code.split('-').pop() }}</a>
                    </template>
                  </td>
                  <td>{{ fmtMinutes(t.estimatedMinutes) }}{{ t.kind === 'recording' ? ' total' : '' }}</td>
                  <td>{{ t.deadline || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </q-card-section>
          <q-card-section v-else class="text-grey-7">Nothing outstanding — every topic is drafted and recorded.</q-card-section>
          <q-separator />

          <q-card-section class="row items-center gap-sm">
            <q-btn outline dense no-caps icon="content_copy" label="Copy calendar link" @click="copyIcsLink(plan.icsUrl)" />
            <q-btn outline dense no-caps icon="event" label="Copy webcal:// link" @click="copyIcsLink(webcalUrl)">
              <q-tooltip max-width="320px">Outlook and most calendar apps treat a webcal:// link as a direct "subscribe" action.</q-tooltip>
            </q-btn>
            <q-space />
            <q-btn outline dense no-caps icon="mail" label="Send reminder now" :loading="reminding" :disable="!plan.ownerEmail" @click="sendReminder">
              <q-tooltip v-if="!plan.ownerEmail">Set an owner email first.</q-tooltip>
            </q-btn>
          </q-card-section>
        </q-card>
      </q-expansion-item>

      <q-card flat bordered>
        <q-card-section class="row items-center gap-sm">
          <strong class="text-caption">Show</strong>
          <q-select v-model="state.filter.wait" dense outlined emit-value map-options :options="waitOptions" style="min-width: 190px" aria-label="Waiting on" />
          <q-checkbox v-model="state.filter.stale" dense label="stale" />
          <q-checkbox v-model="state.filter.blocked" dense label="blocked" />
          <q-toggle v-model="detail" dense label="Show pipeline steps">
            <q-tooltip>One column per step (validate, render, cues, …) for each language</q-tooltip>
          </q-toggle>
          <span class="text-caption text-grey-7">{{ visible.length }} of {{ rows.length }} match</span>
          <q-space />
          <q-btn flat dense no-caps label="Select matching" @click="visible.filter((r) => r.has_dir).forEach((r) => state.selected.add(r.topic))" />
          <q-btn flat dense no-caps label="Clear selection" @click="state.selected.clear()" />
        </q-card-section>
        <q-separator v-if="nSelected" />
        <q-card-section v-if="nSelected" class="row items-center gap-sm bg-grey-2 bulkbar">
          <strong>{{ nSelected }} selected</strong>
          <q-btn-toggle v-model="state.lang" dense no-caps unelevated toggle-color="primary" 
            :options="[{ label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }]" />
          <q-btn v-for="c in BULK" :key="c" outline dense no-caps :label="c"  @click="bulk(c)">
            <q-tooltip max-width="320px">{{ STEP_HELP[c] }} Runs on the selected topics; you confirm first.</q-tooltip>
          </q-btn>
          <q-checkbox v-model="state.force" dense label="force"  />
          <q-space />
          
        </q-card-section>
        <q-card-section class="scroll">
          <table class="await-grid">
            <thead>
              <tr>
                <th></th><th>Topic</th>
                <template v-for="a in AWAITING" :key="a.key">
                  <th class="c">{{ a.label }}<span class="n">{{ rows.length - waitingOn[a.key] }}/{{ rows.length }}</span></th>
                  <th v-if="a.key === 'script'" class="c">Filmed<span class="n">{{ sessionsDone }}/{{ rows.length }}</span></th>
                </template>
                <th class="c sep">Composed<span class="n">en</span></th>
                <template v-if="detail">
                  <th v-for="k in detailCols('en')" :key="`en-${k}`" :class="['c st', { sep: k === detailCols('en')[0] }]">{{ k }}<span class="n">en</span></th>
                </template>
                <th v-if="!detail" class="c sep">Pipeline<span class="n">issues</span></th>
                <template v-for="l in langs" :key="l">
                  <th class="c sep">{{ LANG_NAME[l] }}<span class="n">{{ rows.length - waitingTrans[l] }}/{{ rows.length }}</span></th>
                  <th class="c sep">Composed<span class="n">{{ l }}</span></th>
                  <template v-if="detail"><th v-for="k in detailCols(l)" :key="`${l}-${k}`" class="c st">{{ k }}<span class="n">{{ l }}</span></th></template>
                </template>
              </tr>
            </thead>
            <tbody v-for="g in grid" :key="g.unit">
              <tr :class="['unit-row', { focus: focusUnit === g.unit }]" :data-unit="g.unit">
                <th :colspan="99">
                  {{ g.unit }}<span class="t">{{ m.unit_titles?.[g.unit] || '' }}</span>
                  <span v-if="unitPlan(g.unit)?.deliveryDate" class="plan-tag"
                    :class="unitPlan(g.unit)!.onTrack === false ? 'text-negative' : 'text-positive'">
                    <q-icon name="schedule" size="18px" />
                    due {{ unitPlan(g.unit)!.deliveryDate }}<template v-if="unitPlan(g.unit)!.onTrack !== null"> · {{ unitPlan(g.unit)!.onTrack ? 'on track' : `${plural(unitPlan(g.unit)!.daysBehind, 'day')} behind` }}</template>
                  </span>
                  <span v-else-if="plan" class="plan-tag text-grey-6">no delivery date</span>
                  <q-btn-dropdown v-if="g.activity?.exists" split outline dense no-caps size="sm" label="activity" class="q-ml-md" :to="`/edit-doc/${g.activity.path}`">
                    <q-badge v-if="g.activity.errors" color="negative" floating>{{ g.activity.errors }}</q-badge>
                    <q-badge v-else-if="g.activity.warnings" color="warning" floating>{{ g.activity.warnings }}</q-badge>
                    <q-list>
                      <q-item clickable v-close-popup :to="`/doc/${g.activity.path}`">
                        <q-item-section avatar><q-icon name="visibility" /></q-item-section>
                        <q-item-section>View</q-item-section>
                      </q-item>
                      <q-item clickable v-close-popup :href="fileUrl(g.activity.path, null, `download=${module}-${g.unit}-activity.md`)">
                        <q-item-section avatar><q-icon name="download" color="primary" /></q-item-section>
                        <q-item-section>Download</q-item-section>
                      </q-item>
                      <q-item v-if="g.activity.quiz?.exists" clickable v-close-popup :href="fileUrl(g.activity.quiz.package, null, 'download=1')">
                        <q-item-section avatar><q-icon name="download" :color="g.activity.quiz.stale ? 'warning' : 'primary'" /></q-item-section>
                        <q-item-section>{{ g.activity.quiz.stale ? 'Download QTI (out of date)' : `Download QTI · ${plural(g.activity.quiz.questions, 'question')}` }}</q-item-section>
                      </q-item>
                      <q-item v-if="g.activity.quiz && (!g.activity.quiz.exists || g.activity.quiz.stale)" clickable v-close-popup
                        @click="beacon.runJob('qti', [`${module}/${g.unit}`], { force: true })">
                        <q-item-section avatar><q-icon name="quiz" /></q-item-section>
                        <q-item-section>{{ g.activity.quiz.exists ? 'Update QTI' : 'Export QTI' }}</q-item-section>
                      </q-item>
                    </q-list>
                  </q-btn-dropdown>
                </th>
              </tr>
              <tr v-for="r in g.topics" :key="r.topic" :class="['topic-row', { cloud: isCloud(r), selected: state.selected.has(r.topic), dim: !matches(r) }]">
                <td class="sel">
                  <input v-if="r.has_dir" type="checkbox" :aria-label="`Select ${r.topic}`" :checked="state.selected.has(r.topic)"
                    @change="toggle(r.topic, ($event.target as HTMLInputElement).checked)">
                </td>
                <td>
                  <a :href="`#/topic/${r.topic}`" class="tname" @click="cellClick($event, r)">
                    <strong>{{ r.code }}</strong>{{ isCloud(r) ? ' ☁' : '' }}<span v-if="r.title" class="t">{{ r.title }}</span>
                    <span v-if="r.unreviewed_mistranscriptions" class="unrev">✎{{ r.unreviewed_mistranscriptions }}<q-tooltip>suspected mis-transcriptions to review</q-tooltip></span>
                  </a>
                </td>
                <template v-for="a in AWAITING" :key="a.key">
                <td class="c">
                  <q-icon v-if="have(r, a.key)" :name="a.key === 'script' && scriptIssue(r.en) ? (scriptIssue(r.en) === 'error' ? 'error' : 'warning') : 'check_circle'"
                    :color="a.key === 'script' && scriptIssue(r.en) ? (scriptIssue(r.en) === 'error' ? 'negative' : 'warning') : 'positive'" size="20px">
                    <q-tooltip style="white-space: pre-line">{{ a.help }} received{{ a.key === 'script' && scriptIssue(r.en) ? '\n' + scriptTip(r.en) : '' }}</q-tooltip>
                  </q-icon>
                  <q-icon v-else name="hourglass_empty" color="grey-6" size="20px"><q-tooltip>Waiting for {{ a.help }}</q-tooltip></q-icon>
                </td>
                <td v-if="a.key === 'script'" class="c">
                  <q-checkbox v-if="r.has_dir" dense size="sm" :model-value="sessionDone(r)" :disable="have(r, 'recording')"
                    :aria-label="`Recording session done for ${r.topic}`" @update:model-value="(v: boolean) => toggleSession(r, v)">
                    <q-tooltip>{{ have(r, 'recording') ? 'Recording received, so the session has happened'
                      : r.session ? `Session marked done by ${r.session.by} ${fmtAgo(r.session.at)}` : 'Tick once the recording session has happened' }}</q-tooltip>
                  </q-checkbox>
                </td>
                </template>
                <td class="c sep">
                  <q-btn v-if="composeOutput(r, 'en')" round flat dense size="sm" icon="play_circle"
                    :color="composeOutput(r, 'en')!.draft ? 'warning' : 'positive'" @click="openComposeVideo(r, 'en')">
                    <q-tooltip style="white-space: pre-line">{{ composeOutput(r, 'en')!.draft ? 'Draft preview' : 'Final composed video' }}{{ composeOutput(r, 'en')!.stale ? ' (out of date)' : '' }} · {{ fmtAgo(composeOutput(r, 'en')!.mtime) }}</q-tooltip>
                  </q-btn>
                  <q-icon v-else name="remove" color="grey-5" size="20px"><q-tooltip>No composed video yet</q-tooltip></q-icon>
                </td>
                <template v-if="detail">
                  <td v-for="k in detailCols('en')" :key="`en-${k}`" :class="['c st', { sep: k === detailCols('en')[0] }]">
                    <q-icon v-if="k === 'bumpers'" :name="STEP_ICON[bumperStatus(r, 'en')][0]" :color="STEP_ICON[bumperStatus(r, 'en')][1]" size="18px">
                      <q-tooltip style="white-space: pre-line">{{ bumperTip('EN bumpers', r, 'en') }}</q-tooltip>
                    </q-icon>
                    <q-icon v-else :name="STEP_ICON[stepStatus(r.en.steps[k], k, r.en)][0]" :color="STEP_ICON[stepStatus(r.en.steps[k], k, r.en)][1]" size="18px">
                      <q-tooltip style="white-space: pre-line">{{ stepTip(`EN ${k}`, r.en.steps[k], k, r.en) }}</q-tooltip>
                    </q-icon>
                  </td>
                </template>
                <td v-if="!detail" class="c sep">
                  <q-icon v-if="pipelineSeverity(r)" :name="pipelineSeverity(r) === 'failed' ? 'error' : 'update'" :color="pipelineSeverity(r) === 'failed' ? 'negative' : 'warning'" size="20px">
                    <q-tooltip style="white-space: pre-line">{{ pipelineTip(r) }}</q-tooltip>
                  </q-icon>
                </td>
                <template v-for="l in langs" :key="l">
                <td class="c sep">
                  <q-icon v-if="issue(r[l])" :name="issue(r[l]) === 'error' ? 'error' : 'warning'" :color="issue(r[l]) === 'error' ? 'negative' : 'warning'" size="20px">
                    <q-tooltip style="white-space: pre-line">{{ halfTip(r[l], l) }}{{ '\n' + issueTip(r[l]) }}</q-tooltip>
                  </q-icon>
                  <q-icon v-else-if="translated(r, l)" name="check_circle" color="positive" size="20px"><q-tooltip>{{ LANG_NAME[l] }} copy in the system</q-tooltip></q-icon>
                  <q-icon v-else name="hourglass_empty" color="grey-6" size="20px"><q-tooltip style="white-space: pre-line">{{ halfTip(r[l], l) }}</q-tooltip></q-icon>
                </td>
                <td class="c sep">
                  <q-btn v-if="composeOutput(r, l)" round flat dense size="sm" icon="play_circle"
                    :color="composeOutput(r, l)!.draft ? 'warning' : 'positive'" @click="openComposeVideo(r, l)">
                    <q-tooltip style="white-space: pre-line">{{ composeOutput(r, l)!.draft ? 'Draft preview' : 'Final composed video' }}{{ composeOutput(r, l)!.stale ? ' (out of date)' : '' }} · {{ fmtAgo(composeOutput(r, l)!.mtime) }}</q-tooltip>
                  </q-btn>
                  <q-icon v-else name="remove" color="grey-5" size="20px"><q-tooltip>No composed video yet</q-tooltip></q-icon>
                </td>
                <template v-if="detail">
                  <td v-for="k in detailCols(l)" :key="`${l}-${k}`" class="c st">
                    <q-icon v-if="k === 'bumpers'" :name="STEP_ICON[bumperStatus(r, l)][0]" :color="STEP_ICON[bumperStatus(r, l)][1]" size="18px">
                      <q-tooltip style="white-space: pre-line">{{ bumperTip(`${l.toUpperCase()} bumpers`, r, l) }}</q-tooltip>
                    </q-icon>
                    <q-icon v-else :name="STEP_ICON[stepStatus(r[l].steps[k], k, r[l])][0]" :color="STEP_ICON[stepStatus(r[l].steps[k], k, r[l])][1]" size="18px">
                      <q-tooltip style="white-space: pre-line">{{ stepTip(`${l.toUpperCase()} ${k}`, r[l].steps[k], k, r[l]) }}</q-tooltip>
                    </q-icon>
                  </td>
                </template>
                </template>
              </tr>
            </tbody>
          </table>
        </q-card-section>
      </q-card>
      <p class="text-caption text-grey-7 q-mt-md">
        <q-icon name="check_circle" color="positive" /> received / in the system ·
        <q-icon name="hourglass_empty" color="grey-6" /> waiting ·
        <q-icon name="check_box" color="primary" /> filmed (ticked by hand once the recording session has happened) ·
        <q-icon name="warning" color="warning" /> validation warnings ·
        <q-icon name="error" color="negative" /> errors, blocked or a failed step ·
        <q-icon name="update" color="warning" /> a step is out of date ·
        <q-icon name="play_circle" color="positive" /> composed, final <q-icon name="play_circle" color="warning" /> composed, draft only.
        A ☁ topic has cloud-only files. Click a row to open it; ⌘-click or tick to select.
      </p>

      <q-dialog v-model="videoDialog.open">
        <q-card style="width: 960px; max-width: 95vw;">
          <q-card-section class="row items-center q-pb-sm">
            <div class="text-subtitle1 text-weight-medium">{{ videoDialog.title }}</div>
            <StateChip v-if="videoDialog.draft" kind="warn" label="draft only" class="q-ml-sm" />
            <q-space />
            <q-btn icon="close" flat round dense v-close-popup />
          </q-card-section>
          <q-card-section class="q-pa-none">
            <video v-if="videoDialog.open" :src="videoDialog.src" controls autoplay
              style="width: 100%; max-height: 75vh; display: block; background: black;" />
          </q-card-section>
        </q-card>
      </q-dialog>
    </template>
  </q-page>
</template>

<style scoped>
body.body--dark .bulkbar { background: rgba(255, 255, 255, .04) !important; }
body.body--dark .planning-card :deep(.bg-green-1) { background: rgba(76, 175, 80, .12) !important; }
.plan-tasks { width: 100%; border-collapse: collapse; }
.plan-tasks th { text-align: left; font-weight: normal; color: var(--q-grey-7, #757575); font-size: 12px; padding: 4px 8px; }
.plan-tasks td { padding: 4px 8px; border-top: 1px solid rgba(0, 0, 0, .06); }
/* The browser's own calendar-picker glyph on a native <input type="date"> lives inside the
   UA's own shadow DOM, not ours to lay out — it can't be made to line up with a Quasar
   append icon reliably across browsers. Hidden entirely in favour of the prepend q-icon
   above, which opens the same native picker (via showPicker()) but is laid out by Quasar
   like every other icon in the app. */
.unit-date-input :deep(input[type='date']::-webkit-calendar-picker-indicator) { display: none; }
</style>
