<script setup lang="ts">
// Programme: the landing screen. Four numbers, then one row per module. Rows are links and nothing else.
import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import type { JobSummary, Lang, Milestone, NewModuleRequest, PlansSummaryResponse, TopicStatus } from '@beacon/shared';
import { api } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { plural } from '@/format';
import { markDone, published, publishedStale, readyToTranslate, recordingIn, scripted, subtitlesIn, translated } from '@/progress';
import { useBeacon } from '@/stores/beacon';

const beacon = useBeacon();
const router = useRouter();

// Add module (admin only): creates the module's folder and its Claude Project pack, then opens
// the module with the Project checklist showing. The course map comes later, from the Project.
const addOpen = ref(false);
const adding = ref(false);
const addingJob = ref<number | null>(null);
// Still queued: the server starts it once a job slot is free.
const addQueued = computed(() => addingJob.value !== null && beacon.jobs.get(addingJob.value)?.state === 'queued');
const newModule = ref({ code: '', title: '', units: 10 });
const codeOk = computed(() => /^[A-Z]{2}\d{4}$/.test(newModule.value.code.trim().toUpperCase()));
function openAdd() { newModule.value = { code: '', title: '', units: 10 }; addOpen.value = true; }
async function addModule() {
  const code = newModule.value.code.trim().toUpperCase();
  adding.value = true;
  try {
    const body: NewModuleRequest = { code, title: newModule.value.title.trim() || undefined, units: newModule.value.units };
    const job = await api<JobSummary>('/api/modules', { method: 'POST', body });
    beacon.trackJob(job);
    addingJob.value = job.id;
    const result = await beacon.awaitJob(job.id);
    if (result.state !== 'done') {
      const why = result.envelopes.flatMap((e) => e.diagnostics).find((d) => d.level === 'error')?.message;
      beacon.toast(why ?? `${code} could not be added; see Jobs.`, true);
      return;
    }
    await beacon.loadStatus();
    addOpen.value = false;
    await router.push({ path: `/module/${code}`, query: { project: '1' } });
  } catch (e) {
    beacon.toast((e as Error).message, true);
  } finally {
    adding.value = false;
    addingJob.value = null;
  }
}

// Delivery planning: the next at-risk milestone per module, for a producer scanning
// across every module at once. Full per-topic detail lives on each module's own page.
const MILESTONE_LABEL: Record<string, string> = {
  briefs_done: 'briefs', recorded: 'recorded', ready: 'ready for translation', translated: 'translated', published: 'published',
};
const plans = ref<PlansSummaryResponse>({});
const loadPlans = () => api<PlansSummaryResponse>('/api/plans').then((d) => { plans.value = d; }).catch(() => {});
onMounted(loadPlans);
watch(() => beacon.status, loadPlans);

const today = () => new Date().toISOString().slice(0, 10);
function milestoneChip(name: string): { label: string; kind: string } | null {
  const p = plans.value[name];
  if (!p) return null;
  const next: Milestone | undefined = p.milestones.find((ms) => !ms.done);
  if (!next) return { label: 'delivery-ready', kind: 'ok' };
  const label = next.dueDate ? `${MILESTONE_LABEL[next.kind]} by ${next.dueDate}` : `${next.remaining} to be ${MILESTONE_LABEL[next.kind]}`;
  const overdue = next.dueDate !== null && next.dueDate < today();
  return { label, kind: overdue ? 'blocked' : 'ok' };
}
const milestoneChips = computed(() => {
  const out: Record<string, { label: string; kind: string } | null> = {};
  for (const name of Object.keys(plans.value)) out[name] = milestoneChip(name);
  return out;
});

// Worst-unit-wins on-track status (see planning.ts) — null until at least one unit has an
// agreed delivery date, so a module with none set shows no chip at all.
function onTrackChip(name: string): { label: string; kind: string } | null {
  const p = plans.value[name];
  if (!p || p.onTrack === null) return null;
  return { label: p.onTrack ? 'on track' : `${plural(p.daysBehind, 'day')} behind`, kind: p.onTrack ? 'ok' : 'blocked' };
}
const onTrackChips = computed(() => {
  const out: Record<string, { label: string; kind: string } | null> = {};
  for (const name of Object.keys(plans.value)) out[name] = onTrackChip(name);
  return out;
});

const env = computed(() => beacon.status);
const s = computed(() => env.value!.summary);
const modules = computed(() => Object.entries(env.value?.summary.modules || {}).sort(([a], [b]) => a.localeCompare(b)));

const nPublished = (lang: Lang) => (env.value?.results || []).filter((r) => published(r, lang)).length;
const kpis = computed(() => {
  const jobs = [...beacon.jobs.values()];
  const running = jobs.filter((j) => ['running', 'stalled'].includes(j.state)).length;
  const queued = jobs.filter((j) => j.state === 'queued').length;
  const d = s.value.diagnostics;
  return [
    { v: `${nPublished('en')} / ${nPublished('zh')}`, l: 'Topics published', s: 'English / Mandarin, composed' },
    { v: s.value.blocked, l: 'Topics blocked', s: 'need a person', alert: s.value.blocked > 0 },
    { v: running, l: 'Jobs running', s: queued ? `${queued} queued` : 'none queued' },
    { v: d.error + d.warn, l: 'Diagnostics outstanding', alert: d.error > 0,
      s: `${d.error} errors · ${d.warn} warnings${s.value.unreviewed ? ` · ${s.value.unreviewed} to proofread` : ''}` },
  ];
});

// Production progress, the same model as the module grid: what has been scripted, recorded
// (filmed), edited, translated and published (composed), per module. Each cell shows how
// many topics are done, and underneath, how many are ready for that step right now — the
// work that can be picked up next — so the bottleneck in each module reads at a glance.
type Step = {
  key: string; label: string; lang?: Lang; help: string;
  done: (r: TopicStatus) => boolean;
  next: (r: TopicStatus) => boolean; nextLabel: string;
  also?: { test: (r: TopicStatus) => boolean; label: string };
};
const showZh = computed(() => beacon.boot?.prefs.module_columns?.zh !== false);
const STEPS = computed<Step[]>(() => [
  { key: 'scripted', label: 'Scripted', help: 'Draft script (topic.md) in the system',
    done: scripted, next: (r) => !scripted(r), nextLabel: 'to write' },
  { key: 'recorded', label: 'Recorded', help: 'Recording session has happened (Filmed ticked, or the recording is in)',
    done: (r) => markDone(r, 'filmed'), next: (r) => scripted(r) && !markDone(r, 'filmed'), nextLabel: 'to film' },
  { key: 'edited', label: 'Edited', help: 'Edit done (Edited ticked, or the recording is in)',
    done: (r) => markDone(r, 'edited'), next: (r) => markDone(r, 'filmed') && !markDone(r, 'edited'), nextLabel: 'to edit',
    also: { test: (r) => !!r.marks.edited && !recordingIn(r), label: 'to upload' } },
  // What each unit's delivery date is for (planning.ts): subtitles in, proofread, ready to send.
  { key: 'ready', label: 'Ready to translate', help: 'English subtitles in, prepared and every mishearing reviewed (or already sent)',
    done: readyToTranslate, next: (r) => subtitlesIn(r) && !readyToTranslate(r), nextLabel: 'to check',
    also: { test: (r) => !readyToTranslate(r) && r.unreviewed_mistranscriptions > 0, label: 'to proofread' } },
  ...(showZh.value ? [{ key: 'translated', label: 'Translated', lang: 'zh' as Lang, help: 'Mandarin script and subtitles back from translation',
    done: (r: TopicStatus) => translated(r, 'zh'), next: (r: TopicStatus) => r.zh.next === 'translation_export', nextLabel: 'to send',
    also: { test: (r: TopicStatus) => r.zh.stage === 'out_for_translation', label: 'out' } }] : []),
  { key: 'published-en', label: 'Published', lang: 'en', help: 'English video composed (final, not a draft)',
    done: (r) => published(r, 'en'), next: (r) => recordingIn(r) && !published(r, 'en'), nextLabel: 'to compose' },
  ...(showZh.value ? [{ key: 'published-zh', label: 'Published', lang: 'zh' as Lang, help: 'Mandarin video composed (final, not a draft)',
    done: (r: TopicStatus) => published(r, 'zh'), next: (r: TopicStatus) => recordingIn(r) && translated(r, 'zh') && !published(r, 'zh'),
    nextLabel: 'to compose' }] : []),
]);
const topicsOf = (module?: string) => (env.value?.results || []).filter((r) => !module || r.module === module);
function cells(module?: string) {
  const rows = topicsOf(module);
  return STEPS.value.map((st) => {
    const n = rows.filter(st.done).length;
    return {
      ...st, n, total: rows.length, pct: rows.length ? (100 * n) / rows.length : 0,
      ready: rows.filter((r) => !st.done(r) && st.next(r)).length,
      extra: st.also ? rows.filter(st.also.test).length : 0,
      stale: st.key.startsWith('published') ? rows.filter((r) => publishedStale(r, st.lang!)).length : 0,
    };
  });
}
const totals = computed(() => cells());
const gridStyle = computed(() => ({ gridTemplateColumns: `repeat(${STEPS.value.length}, minmax(72px, 1fr))` }));
</script>

<template>
  <q-page padding class="page-max">
    <div v-if="!env" class="text-grey-7 q-pa-lg">Reading the programme…</div>
    <template v-else>
      <PageHeader title="Programme" :sub="`${s.topics} topics across ${modules.length} modules`">
        <q-btn v-if="beacon.boot?.admin" outline dense no-caps icon="add" label="Add module" @click="openAdd" />
      </PageHeader>

      <q-dialog v-model="addOpen">
        <q-card style="width: 480px; max-width: 95vw">
          <q-card-section class="text-h6">Add a module</q-card-section>
          <q-card-section class="q-pt-none column q-gutter-y-md">
            <div class="text-body2">
              Creates the module in NUCS and the Claude Project its leader is interviewed in: the instructions to paste and the
              templates to upload. The course map comes later, from the Project.
            </div>
            <q-input v-model="newModule.code" outlined dense autofocus label="Module code" hint="e.g. KV7023" maxlength="6"
              :error="!!newModule.code && !codeOk" error-message="Two letters and four digits"
              @update:model-value="(v) => (newModule.code = String(v ?? '').toUpperCase())" />
            <q-input v-model="newModule.title" outlined dense label="Title (optional)" maxlength="120" />
            <q-input v-model.number="newModule.units" outlined dense type="number" min="1" max="30" label="Units"
              hint="One unit is one week of study" />
          </q-card-section>
          <q-card-actions align="right">
            <span v-if="addQueued" class="text-caption text-grey-7 q-mr-auto q-pl-sm">Waiting for a free job slot (see Jobs)…</span>
            <q-btn flat no-caps label="Cancel" v-close-popup />
            <q-btn unelevated no-caps color="primary" label="Add module" :disable="!codeOk || !(newModule.units >= 1 && newModule.units <= 30)"
              :loading="adding" @click="addModule" />
          </q-card-actions>
        </q-card>
      </q-dialog>

      <div class="row q-col-gutter-md q-mb-md">
        <div v-for="k in kpis" :key="k.l" class="col-12 col-sm-6 col-md-3">
          <q-card flat bordered>
            <q-card-section>
              <div :class="['text-h4 text-weight-bold', { 'text-negative': k.alert }]">{{ k.v }}</div>
              <div>{{ k.l }}</div>
              <div class="text-caption text-grey-7">{{ k.s }}</div>
            </q-card-section>
          </q-card>
        </div>
      </div>

      <q-card flat bordered>
        <q-list separator>
          <!-- Column headings, with programme totals: laid out exactly like a module row, so the columns line up. -->
          <q-item dense class="q-pt-sm">
            <q-item-section class="prog-name"><q-item-label caption>All modules</q-item-label></q-item-section>
            <q-item-section>
              <div class="ladder" :style="gridStyle">
                <span v-for="c in totals" :key="c.key" :class="['ladder-h', { zh: c.lang === 'zh' }]">
                  <strong>{{ c.label }}</strong><template v-if="c.lang"> · {{ c.lang.toUpperCase() }}</template>
                  <br>{{ c.n }}/{{ c.total }}
                  <q-tooltip>{{ c.help }}</q-tooltip>
                </span>
              </div>
            </q-item-section>
            <q-item-section side class="prog-flags"></q-item-section>
            <q-item-section side class="prog-track"></q-item-section>
          </q-item>
          <q-item v-for="[name, m] in modules" :key="name" clickable :to="`/module/${name}`" class="q-py-md">
            <q-item-section class="prog-name">
              <q-item-label class="text-weight-bold">{{ name }}</q-item-label>
              <q-item-label caption>{{ m.title }}</q-item-label>
            </q-item-section>
            <q-item-section>
              <div class="ladder" :style="gridStyle">
                <div v-for="c in cells(name)" :key="c.key">
                  <div :class="['ladder-cell', c.lang ?? 'en', { done: c.n === c.total && c.n > 0 }]">
                    <span class="fill" :style="{ width: `${c.pct}%` }"></span>
                    <span class="n">{{ c.n }}/{{ c.total }}</span>
                    <q-tooltip>{{ c.help }}: {{ c.n }} of {{ c.total }} topics</q-tooltip>
                  </div>
                  <div class="ladder-next">
                    <span v-if="c.ready" class="ready">{{ c.ready }} {{ c.nextLabel }}</span>
                    <span v-if="c.extra">{{ c.extra }} {{ c.also!.label }}</span>
                    <span v-if="c.stale" class="stale">{{ c.stale }} out of date</span>
                  </div>
                </div>
              </div>
            </q-item-section>
            <q-item-section side class="prog-flags row items-center gap-xs" style="flex-direction: row; flex-wrap: wrap; justify-content: flex-end; align-content: center">
              <StateChip v-if="milestoneChips[name]" :kind="milestoneChips[name]!.kind" :label="milestoneChips[name]!.label">
                <q-tooltip>Next delivery-planning milestone · see the module page for details</q-tooltip>
              </StateChip>
              <StateChip v-if="m.blocked" kind="blocked" :label="`${m.blocked} blocked`" />
              <StateChip v-if="m.stale" kind="stale" :label="`${m.stale} stale`" />
              <StateChip v-if="m.cloud" kind="cloud" :label="`☁ ${m.cloud} cloud-only`" />
              <StateChip v-if="m.unreviewed" kind="warn" :label="`${m.unreviewed} to proofread`" />
              <StateChip v-if="m.errors" kind="error" :label="`module documents: ${plural(m.errors, 'error')}`" />
              <StateChip v-if="m.claude_project?.stale" kind="warn" label="Claude Project out of date" />
            </q-item-section>
            <q-item-section v-if="onTrackChips[name]" side class="prog-track">
              <q-icon name="schedule" size="32px" :color="onTrackChips[name]!.kind === 'ok' ? 'positive' : 'negative'">
                <q-tooltip>{{ onTrackChips[name]!.label }} · delivery status across this module's units, worst unit wins</q-tooltip>
              </q-icon>
            </q-item-section>
          </q-item>
          <q-item v-if="!modules.length"><q-item-section class="text-grey-7">No modules found.</q-item-section></q-item>
        </q-list>
      </q-card>
    </template>
  </q-page>
</template>
