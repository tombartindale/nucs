<script setup lang="ts">
// Module: units down, topics across, one split cell per topic (English | Mandarin).
// Stage is shown by position; colour marks only stale and blocked. Bulk actions live here,
// where the scope is visible, and say what they will do before they run.
import { computed, onMounted, reactive, ref, watch } from 'vue';
import type { LangState, ModulePlanResponse, TopicStatus } from '@beacon/shared';
import PageHeader from '@/components/PageHeader.vue';
import StagePips from '@/components/StagePips.vue';
import StateChip from '@/components/StateChip.vue';
import { api, fileUrl } from '@/api';
import { confirm } from '@/composables/confirm';
import { fmtMinutes, LANG_NAME, plural, STAGE_LABEL, STEP_HELP, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

const BULK = ['validate', 'render', 'script', 'bumpers', 'cues', 'compose', 'package'];
const props = defineProps<{ module: string }>();
const beacon = useBeacon();

const state = reactive({
  selected: new Set<string>(),
  lang: 'en' as 'en' | 'zh',    // language for bulk actions
  force: false,
  filter: { stage: '', lang: 'en' as 'en' | 'zh' | 'both', stale: false, blocked: false },
});

const env = computed(() => beacon.status);
const rows = computed(() => (env.value?.results || []).filter((r) => r.module === props.module));
const m = computed(() => env.value?.summary.modules[props.module]);
const units = computed(() => [...new Set(rows.value.map((r) => r.unit))].sort());
const codes = computed(() => [...new Set(rows.value.map((r) => r.code))].sort());
const grid = computed(() => units.value.map((unit) => ({
  unit, cells: codes.value.map((c) => rows.value.find((x) => x.unit === unit && x.code === c) ?? null),
  activity: (m.value?.documents || []).find((d) => d.path === `${props.module}/${unit}/activity.md`) ?? null,
})));
const stageOptions = computed(() => [{ label: 'any stage', value: '' },
  ...[...new Set([...(rows.value[0]?.en.stages || []), ...(rows.value[0]?.zh.stages || [])])].map((s) => ({ label: STAGE_LABEL[s], value: s }))]);
const cols = computed(() => beacon.boot?.prefs.module_columns || { en: true, zh: true });
const langs = computed(() => (['en', 'zh'] as const).filter((l) => cols.value[l] !== false));

function matches(r: TopicStatus) {
  const f = state.filter;
  const sides = f.lang === 'both' ? [r.en, r.zh] : [r[f.lang]];
  if (f.stage && !sides.some((s) => s.stage === f.stage)) return false;
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
const planForm = reactive({ deliveryDate: '', ownerName: '', ownerEmail: '' });
const planSaving = ref(false);
const reminding = ref(false);

function syncPlanForm() {
  if (!plan.value) return;
  planForm.deliveryDate = plan.value.deliveryDate ?? '';
  planForm.ownerName = plan.value.ownerName;
  planForm.ownerEmail = plan.value.ownerEmail;
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
      body: { deliveryDate: planForm.deliveryDate || null, ownerName: planForm.ownerName, ownerEmail: planForm.ownerEmail },
    });
    beacon.toast('Delivery plan saved.');
  } catch (e) {
    beacon.toast((e as Error).message, true);
  } finally {
    planSaving.value = false;
  }
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
      <PageHeader :title="`${module}${m.title ? ' · ' + m.title : ''}`" :crumbs="[{ label: 'Programme', to: '/' }, { label: module }]"
        :sub="`${m.topics} topics · English ${m.complete.en} complete · Mandarin ${m.complete.zh} complete`">
        <StateChip v-if="m.blocked" kind="blocked" :label="`${m.blocked} blocked`" />
        <StateChip v-if="m.stale" kind="stale" :label="`${m.stale} stale`" />
        <StateChip v-if="m.cloud" kind="cloud" :label="`☁ ${m.cloud} cloud-only`" />
        <q-btn v-if="quizzes.length" outline no-caps icon="quiz" label="Export quizzes to LMS" @click="exportQuizzes">
          <q-tooltip max-width="320px">{{ STEP_HELP.qti }}</q-tooltip>
        </q-btn>
        <q-btn outline no-caps label="Run QA on module" @click="beacon.runJob('qa', [module], {})" />
        <template v-if="moduleMap">
          <q-btn-dropdown v-if="moduleMap.exists" split outline dense no-caps label="Module map" :to="`/edit-doc/${moduleMap.path}`">
            <q-badge v-if="moduleMap.errors" color="negative" floating>{{ moduleMap.errors }}</q-badge>
            <q-badge v-else-if="moduleMap.warnings" color="warning" floating>{{ moduleMap.warnings }}</q-badge>
            <q-list>
              <q-item clickable v-close-popup :to="`/doc/${moduleMap.path}`">
                <q-item-section avatar><q-icon name="visibility" /></q-item-section>
                <q-item-section>View</q-item-section>
              </q-item>
              <q-item v-if="moduleMap.pdf?.exists" clickable v-close-popup :href="fileUrl(moduleMap.pdf.path, null, 'download=1')">
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
              {{ plan.deliveryDate ? `Delivery ${plan.deliveryDate}` : 'No delivery date set' }}
              <span v-if="plan.ownerName || plan.ownerEmail"> · {{ plan.ownerName }}{{ plan.ownerName && plan.ownerEmail ? ' · ' : '' }}{{ plan.ownerEmail }}</span>
            </q-item-label>
          </q-item-section>
          <StateChip v-if="plan.plan.onTrack !== null" :kind="plan.plan.onTrack ? 'ok' : 'blocked'"
            :label="plan.plan.onTrack ? 'on track' : `${plural(plan.plan.daysBehind, 'day')} behind`" />
        </template>
        <q-card flat bordered>
          <q-card-section class="row items-end gap-md">
            <q-input v-model="planForm.deliveryDate" type="date" dense outlined label="Delivery date" style="max-width: 200px" />
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

          <!-- Task table: the content creator's detail view, outstanding briefs/recordings in order. -->
          <q-card-section v-if="plan.plan.tasks.length">
            <table class="plan-tasks">
              <thead><tr><th>Topic</th><th>Task</th><th>Est.</th><th>Deadline</th></tr></thead>
              <tbody>
                <tr v-for="t in plan.plan.tasks" :key="`${t.topic}-${t.kind}`">
                  <td><a :href="`#/topic/${t.topic}`">{{ t.topic }}</a> {{ t.title }}</td>
                  <td>{{ t.kind === 'brief' ? 'Write brief' : 'Record' }}</td>
                  <td>{{ fmtMinutes(t.estimatedMinutes) }}</td>
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
          <q-select v-model="state.filter.stage" dense outlined emit-value map-options :options="stageOptions" style="min-width: 150px" aria-label="Stage" />
          <q-select v-model="state.filter.lang" dense outlined emit-value map-options style="min-width: 150px" aria-label="Language"
            :options="[{ label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }, { label: 'either language', value: 'both' }]" />
          <q-checkbox v-model="state.filter.stale" dense label="stale" />
          <q-checkbox v-model="state.filter.blocked" dense label="blocked" />
          <span class="text-caption text-grey-7">{{ visible.length }} of {{ rows.length }} match</span>
          <q-space />
          <q-btn flat dense no-caps label="Select matching" @click="visible.filter((r) => r.has_dir).forEach((r) => state.selected.add(r.topic))" />
          <q-btn flat dense no-caps label="Clear selection" @click="state.selected.clear()" />
        </q-card-section>
        <q-separator />
        <q-card-section class="row items-center gap-sm bg-grey-2 bulkbar">
          <strong>{{ nSelected ? `${nSelected} selected` : 'Select topics to act on them' }}</strong>
          <q-btn-toggle v-model="state.lang" dense no-caps unelevated toggle-color="primary" :disable="!nSelected"
            :options="[{ label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }]" />
          <q-btn v-for="c in BULK" :key="c" outline dense no-caps :label="c" :disable="!nSelected" @click="bulk(c)">
            <q-tooltip max-width="320px">{{ STEP_HELP[c] }} Runs on the selected topics; you confirm first.</q-tooltip>
          </q-btn>
          <q-checkbox v-model="state.force" dense label="force" :disable="!nSelected" />
          <q-space />
          <span class="text-caption text-grey-7">Click a cell to open it; ⌘-click or tick to select.</span>
        </q-card-section>
        <q-card-section class="scroll">
          <table class="topic-grid">
            <thead><tr><th></th><th v-for="c in codes" :key="c">{{ c }}</th><th>Activity</th></tr></thead>
            <tbody>
              <tr v-for="row in grid" :key="row.unit">
                <th class="unit">
                  {{ row.unit }}<span class="t">{{ m.unit_titles?.[row.unit] || '' }}</span>
                </th>
                <td v-for="(r, i) in row.cells" :key="codes[i]">
                  <a v-if="r" :href="`#/topic/${r.topic}`" @click="cellClick($event, r)"
                    :class="['cell', { one: langs.length === 1, cloud: isCloud(r), planned: r.en.stage === 'planned', selected: state.selected.has(r.topic), dim: !matches(r) }]">
                    <span class="code">{{ r.code }}{{ isCloud(r) ? ' ☁' : '' }}</span>
                    <input v-if="r.en.stage !== 'planned'" type="checkbox" class="sel" :aria-label="`Select ${r.topic}`"
                      :checked="state.selected.has(r.topic)" @click.stop="toggle(r.topic, ($event.target as HTMLInputElement).checked)">
                    <div v-for="lang in langs" :key="lang" :class="['half', { stale: r[lang].stale, blocked: r[lang].blocked }]">
                      <StagePips :index="r[lang].stage_index" :total="r[lang].stages.length" />
                      <span class="lbl">{{ STAGE_LABEL[r[lang].stage] }}</span>
                      <q-tooltip style="white-space: pre-line">{{ r.topic }}{{ r.title ? ` — ${r.title}` : '' }}{{ isCloud(r) ? '\ncloud-only files' : '' }}{{ '\n' + halfTip(r[lang], lang) }}</q-tooltip>
                    </div>
                    <span v-if="r.unreviewed_mistranscriptions" class="unrev">✎{{ r.unreviewed_mistranscriptions }}<q-tooltip>suspected mis-transcriptions to review</q-tooltip></span>
                  </a>
                </td>
                <td>
                  <div v-if="row.activity?.exists" class="cell one activity">
                    <q-btn-dropdown split outline dense no-caps size="sm" label="activity" :to="`/edit-doc/${row.activity.path}`">
                      <q-badge v-if="row.activity.errors" color="negative" floating>{{ row.activity.errors }}</q-badge>
                      <q-badge v-else-if="row.activity.warnings" color="warning" floating>{{ row.activity.warnings }}</q-badge>
                      <q-list>
                        <q-item clickable v-close-popup :to="`/doc/${row.activity.path}`">
                          <q-item-section avatar><q-icon name="visibility" /></q-item-section>
                          <q-item-section>View</q-item-section>
                        </q-item>
                        <q-item v-if="row.activity.quiz?.exists" clickable v-close-popup :href="fileUrl(row.activity.quiz.package, null, 'download=1')">
                          <q-item-section avatar><q-icon name="download" :color="row.activity.quiz.stale ? 'warning' : 'primary'" /></q-item-section>
                          <q-item-section>{{ row.activity.quiz.stale ? 'Download QTI (out of date)' : `Download QTI · ${plural(row.activity.quiz.questions, 'question')}` }}</q-item-section>
                        </q-item>
                        <q-item v-if="row.activity.quiz && (!row.activity.quiz.exists || row.activity.quiz.stale)" clickable v-close-popup
                          @click="beacon.runJob('qti', [`${module}/${row.unit}`], { force: true })">
                          <q-item-section avatar><q-icon name="quiz" /></q-item-section>
                          <q-item-section>{{ row.activity.quiz.exists ? 'Update QTI' : 'Export QTI' }}</q-item-section>
                        </q-item>
                      </q-list>
                    </q-btn-dropdown>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </q-card-section>
      </q-card>
      <p class="text-caption text-grey-7 q-mt-md">
        Each cell: English on the left, Mandarin on the right. Filled pips show how far the topic has got.
        <StateChip kind="stale" label="amber" /> is stale (built from older inputs);
        <StateChip kind="blocked" label="red" /> is blocked (needs a person); a dashed cell has cloud-only files.
      </p>
    </template>
  </q-page>
</template>

<style scoped>
body.body--dark .bulkbar { background: rgba(255, 255, 255, .04) !important; }
body.body--dark .planning-card :deep(.bg-green-1) { background: rgba(76, 175, 80, .12) !important; }
.plan-tasks { width: 100%; border-collapse: collapse; }
.plan-tasks th { text-align: left; font-weight: normal; color: var(--q-grey-7, #757575); font-size: 12px; padding: 4px 8px; }
.plan-tasks td { padding: 4px 8px; border-top: 1px solid rgba(0, 0, 0, .06); }
</style>
