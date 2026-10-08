<script setup lang="ts">
// Diagnostics: everything outstanding, grouped by code, because failures cluster and
// fixing a class of them at once is how the work goes. Mis-transcriptions get their own
// tab: a proofreading task, with both readings side by side.
import { computed, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue';
import { useRouter } from 'vue-router';
import type { Diagnostic, DiagnosticsResponse, TopicResponse } from '@beacon/shared';
import { api, fileUrl } from '@/api';
import AckControls from '@/components/AckControls.vue';
import DiagnosticItem from '@/components/DiagnosticItem.vue';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { fmtTime, LEVEL_ORDER, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

const props = defineProps<{ tab: string; topic?: string; module?: string }>();
const beacon = useBeacon();
const router = useRouter();
const f = reactive({ level: 'warn', lang: '', code: '' });
const env = shallowRef<DiagnosticsResponse | null>(null);
const error = ref<string | null>(null);
const corrections = reactive<Record<string, string>>({});  // mis-transcription id -> corrected text being typed

// Topic and module are the same idea (narrow to part of the programme) at two granularities,
// so they share one query-driven scope: at most one is set, it lives in the URL (shareable,
// survives reload), and it renders as one chip, not two different filter UIs.
const scope = computed(() => (props.topic ? { kind: 'Topic', value: props.topic } : props.module ? { kind: 'Module', value: props.module } : null));
function inScope(d: Diagnostic) {
  return props.topic ? d.topic === props.topic : !props.module || (d.topic || d.file || '').startsWith(props.module);
}
function go(overrides: { tab?: string; topic?: string; module?: string }) {
  const nextTab = overrides.tab ?? props.tab;
  const topic = 'topic' in overrides ? overrides.topic : props.topic;
  const module = 'module' in overrides ? overrides.module : props.module;
  const query: Record<string, string> = topic ? { topic } : module ? { module } : {};
  void router.push({ path: nextTab === 'mistranscriptions' ? '/diagnostics/mistranscriptions' : '/diagnostics', query });
}

async function load() {
  try { env.value = await api<DiagnosticsResponse>('/api/diagnostics?path=.'); error.value = null; } catch (e) { error.value = (e as Error).message; }
}
void load();
let timer: ReturnType<typeof setTimeout> | undefined;
watch(() => beacon.status, () => { clearTimeout(timer); timer = setTimeout(load, 300); });
onBeforeUnmount(() => clearTimeout(timer));

const outstanding = computed(() => env.value?.outstanding || []);
const moduleOptions = computed(() => [{ label: 'all modules', value: '' }, ...Object.keys(beacon.status?.summary.modules || {}).sort().map((m) => ({ label: m, value: m }))]);
const codeOptions = computed(() => [{ label: 'all codes', value: '' }, ...[...new Set(outstanding.value.map((d) => d.code))].sort().map((c) => ({ label: c, value: c }))]);
const mtOpen = computed(() => outstanding.value.filter((d) => d.code === 'CUE_MISTRANSCRIPTION' && !d.data?.review).length);
const tab = computed({
  get: () => props.tab,
  set: (v: string) => go({ tab: v }),
});

const groups = computed(() => {
  const list = outstanding.value.filter((d) =>
    LEVEL_ORDER[d.level] <= LEVEL_ORDER[f.level] &&
    inScope(d) &&
    (!f.lang || d.lang === f.lang) &&
    (!f.code || d.code === f.code) &&
    (d.code !== 'CUE_MISTRANSCRIPTION' || d.level !== 'info'));
  const byCode = new Map<string, Diagnostic[]>();
  for (const d of list) {
    if (!byCode.has(d.code)) byCode.set(d.code, []);
    byCode.get(d.code)!.push(d);
  }
  return [...byCode.entries()]
    .sort((a, b) => (LEVEL_ORDER[a[1][0].level] - LEVEL_ORDER[b[1][0].level]) || (b[1].length - a[1].length))
    .map(([code, items]) => {
      const topics = [...new Set(items.map((d) => d.topic).filter((t): t is string => Boolean(t)))].sort();
      const steps = [...new Set(items.map((d) => d.data?.step).filter(Boolean))] as string[];
      const langs = [...new Set(items.map((d) => d.lang).filter(Boolean))] as string[];
      const only = steps.length === 1 ? steps[0] : null;
      return { code, items, topics, langs, step: only && topics.length && only !== 'qa' ? only : null };
    });
});

function rerun(g: (typeof groups.value)[number]) {
  const lang = g.step === 'cues' ? 'en' : (g.langs.length === 1 ? g.langs[0] : 'en');
  void beacon.runJob(g.step!, g.topics.map(topicPath), { lang });
}

const mt = computed(() => {
  const items = outstanding.value.filter((d) => d.code === 'CUE_MISTRANSCRIPTION' && inScope(d));
  return [
    { title: 'To review', rows: items.filter((d) => !d.data?.review), empty: 'All reviewed.' },
    { title: 'Reviewed', rows: items.filter((d) => d.data?.review), empty: '' },
  ];
});
async function review(d: Diagnostic, args: Record<string, string | boolean>) {
  try {
    const job = await beacon.runJob('review', [topicPath(d.topic!)], { item: d.data!.id!, ...args });
    const result = await beacon.awaitJob(job.id);
    if (result.state === 'done') await load();
    else beacon.toast('That decision did not save; see Jobs.', true);
  } catch { /* beacon.runJob already toasted the submit failure */ }
}
function correct(d: Diagnostic) {
  const text = (corrections[d.data!.id!] ?? d.data!.srt ?? '').trim();
  if (text) void review(d, { correct: text });
}
function useScript(d: Diagnostic) {
  const text = (d.data!.script ?? '').trim();
  if (text) void review(d, { correct: text });
}

// Play a mis-transcription in place, instead of sending the reviewer to the full TopicPage
// (which loads the whole editor just to seek a video): a hidden <audio> element reused across
// rows, fed the topic's master video file (an <audio> element plays just its audio track fine),
// seeked to the cue and stopped again after a few seconds.
const PLAY_SECONDS = 6;
const audio = ref<HTMLAudioElement | null>(null);
const nowPlaying = ref<string | null>(null);
const mediaCache = reactive<Record<string, string | null>>({});
let loadedSrc: string | null = null;
let stopTimer: ReturnType<typeof setTimeout> | undefined;

async function mediaSrc(topic: string): Promise<string | null> {
  if (!(topic in mediaCache)) {
    try {
      const t = await api<TopicResponse>(`/api/topic/${topic}`);
      const master = t.show.results?.[0]?.media.master;
      mediaCache[topic] = master ? fileUrl(master) : null;
    } catch { mediaCache[topic] = null; }
  }
  return mediaCache[topic];
}
function stopSegment() {
  clearTimeout(stopTimer);
  audio.value?.pause();
  nowPlaying.value = null;
}
async function playSegment(d: Diagnostic) {
  const time = d.data?.time;
  const id = d.data?.id;
  if (time === null || time === undefined || !id || !d.topic) return;
  if (nowPlaying.value === id) { stopSegment(); return; }
  const src = await mediaSrc(d.topic);
  const el = audio.value;
  if (!src || !el) { beacon.toast('No video found for this topic yet.', true); return; }
  clearTimeout(stopTimer);
  nowPlaying.value = id;
  const begin = () => {
    el.currentTime = time;
    void el.play();
    stopTimer = setTimeout(stopSegment, PLAY_SECONDS * 1000);
  };
  if (loadedSrc === src) begin();
  else { loadedSrc = src; el.src = src; el.addEventListener('loadedmetadata', begin, { once: true }); }
}
onBeforeUnmount(stopSegment);
</script>

<template>
  <q-page padding class="page-max">
    <audio ref="audio" style="display: none" @ended="stopSegment" />
    <PageHeader title="Verification"
      :sub="env ? `${env.counts.error} errors · ${env.counts.warn} warnings · ${env.counts.info} info, from current step results` : 'Loading…'" />
    <q-banner v-if="error" class="bg-negative text-white q-mb-md" rounded>{{ error }}</q-banner>
    <div class="row items-center q-mb-md">
      <q-tabs v-model="tab" dense no-caps align="left" active-color="primary" indicator-color="primary">
        <q-tab name="codes" label="By code" />
        <q-tab name="mistranscriptions" :label="`Mis-transcriptions${mtOpen ? ` (${mtOpen})` : ''}`" />
      </q-tabs>
      <q-space />
      <q-btn flat dense no-caps icon="refresh" label="Refresh" @click="load" />
    </div>
    <q-card flat bordered class="q-mb-md">
      <q-card-section class="row items-center gap-sm">
        <q-chip v-if="scope" dense outline color="primary" text-color="primary" icon="filter_alt" removable
          :label="`${scope.kind}: ${scope.value}`" @remove="go({ topic: '', module: '' })" />
        <q-select v-if="tab === 'codes'" v-model="f.level" dense outlined emit-value map-options style="min-width: 190px" aria-label="Level"
          :options="[{ label: 'errors', value: 'error' }, { label: 'errors and warnings', value: 'warn' }, { label: 'everything', value: 'info' }]" />
        <q-select v-if="!scope" :model-value="props.module || ''" dense outlined emit-value map-options :options="moduleOptions" style="min-width: 150px" aria-label="Module"
          @update:model-value="(v) => go({ module: String(v || '') })" />
        <q-select v-if="tab === 'codes'" v-model="f.lang" dense outlined emit-value map-options style="min-width: 150px" aria-label="Language"
          :options="[{ label: 'both languages', value: '' }, { label: 'English', value: 'en' }, { label: 'Mandarin', value: 'zh' }]" />
        <q-select v-if="tab === 'codes'" v-model="f.code" dense outlined emit-value map-options :options="codeOptions" style="min-width: 220px" aria-label="Code" />
      </q-card-section>
    </q-card>

    <div v-if="!env" class="text-grey-7">Loading…</div>

    <template v-else-if="tab === 'codes'">
      <q-card v-if="!groups.length" flat bordered><q-card-section class="text-grey-7">Nothing outstanding at this level.</q-card-section></q-card>
      <q-card v-for="g in groups" :key="g.code" flat bordered class="q-mb-sm">
        <q-expansion-item>
          <template #header>
            <q-item-section avatar><q-badge color="grey-7" :label="g.items.length" /></q-item-section>
            <q-item-section>
              <q-item-label class="row items-center gap-sm"><StateChip :kind="g.items[0].level" /><code>{{ g.code }}</code></q-item-label>
              <q-item-label caption>{{ beacon.codes.get(g.code)?.description || '' }}</q-item-label>
            </q-item-section>
            <q-item-section v-if="g.step" side>
              <q-btn outline dense size="sm" no-caps :label="`Re-run ${g.step} on ${g.topics.length}`" @click.stop="rerun(g)" />
            </q-item-section>
          </template>
          <q-card-section class="q-gutter-xs q-pt-none">
            <q-chip v-for="t in g.topics" :key="t" dense clickable outline :label="t" @click="$router.push(`/topic/${t}`)" />
          </q-card-section>
          <q-list separator>
            <DiagnosticItem v-for="(d, i) in g.items.slice(0, 200)" :key="i" :d="d" :show-code="false" show-lang>
              <template #message>
                <router-link v-if="d.topic" :to="`/topic/${d.topic}`">{{ d.topic }}</router-link>
                <router-link v-else-if="d.data?.document && d.file" :to="`/doc/${d.file}${d.line ? `?line=${d.line}` : ''}`">{{ d.file }}</router-link>
                {{ d.topic || (d.data?.document && d.file) ? ' · ' : '' }}{{ d.message }}
              </template>
              <AckControls :d="d" />
            </DiagnosticItem>
          </q-list>
        </q-expansion-item>
      </q-card>
    </template>

    <template v-else>
      <p class="text-grey-7">The partner translates from this SRT, so a mishearing here reaches Mandarin. Listen, then click whichever
        reading is actually correct — <strong>Script</strong> or <strong>SRT</strong> — to deliver that text; or type the exact words on the
        right if neither is right. Timings never change — only the delivered text. Run subtitles and package afterwards. A decision takes a
        moment to save — the item moves to Reviewed below once it has.</p>
      <q-card v-if="mt[0].rows.length" flat bordered class="q-mb-md">
        <q-card-section class="text-subtitle1 text-weight-medium q-pb-sm">{{ mt[0].title }} ({{ mt[0].rows.length }})</q-card-section>
        <q-list separator>
          <q-item class="text-caption text-grey-6">
            <q-item-section style="max-width: 190px" />
            <q-item-section class="mt-col">Script (approved)</q-item-section>
            <q-item-section class="mt-col">SRT (captioned)</q-item-section>
            <q-item-section side style="width: 300px" />
            <q-item-section side style="width: 104px" />
          </q-item>
          <q-item v-for="d in mt[0].rows" :key="d.data!.id">
            <q-item-section style="max-width: 190px">
              <router-link :to="`/topic/${d.topic}`">{{ d.topic }}</router-link>
              <q-item-label caption>slide {{ d.data!.slide }} · cue {{ d.data!.cue ?? '—' }}</q-item-label>
            </q-item-section>
            <q-item-section class="mt-col pick" @click="useScript(d)">
              <q-item-label>{{ d.data!.script }}</q-item-label>
              <q-tooltip>Click to deliver this as the subtitle</q-tooltip>
            </q-item-section>
            <q-item-section class="mt-col pick" @click="review(d, { accept: true })">
              <q-item-label class="text-warning">{{ d.data!.srt }}</q-item-label>
              <q-tooltip>Click to deliver this as the subtitle</q-tooltip>
            </q-item-section>
            <q-item-section side style="width: 300px">
              <div class="row items-center gap-sm no-wrap">
                <q-input :model-value="corrections[d.data!.id!] ?? d.data!.srt" dense outlined class="col" aria-label="Corrected subtitle text"
                  @update:model-value="(v) => (corrections[d.data!.id!] = String(v ?? ''))" />
                <q-btn unelevated dense color="primary" no-caps label="Correct" class="correct-btn" @click="correct(d)">
                  <q-tooltip>Deliver the subtitle as this typed text</q-tooltip>
                </q-btn>
              </div>
            </q-item-section>
            <q-item-section side style="width: 104px">
              <q-btn v-if="d.data!.time !== null && d.data!.time !== undefined" flat dense no-caps color="primary"
                :icon="nowPlaying === d.data!.id ? 'stop' : 'play_arrow'" :label="nowPlaying === d.data!.id ? 'Stop' : fmtTime(d.data!.time)"
                @click="playSegment(d)" />
            </q-item-section>
          </q-item>
        </q-list>
      </q-card>
      <q-card v-if="!mt[0].rows.length && !mt[1].rows.length" flat bordered><q-card-section class="text-grey-7">No suspected mis-transcriptions.</q-card-section></q-card>
      <q-card v-if="!mt[0].rows.length && mt[1].rows.length" flat bordered class="q-mb-md"><q-card-section class="text-grey-7">All reviewed.</q-card-section></q-card>
      <q-card v-if="mt[1].rows.length" flat bordered>
        <q-expansion-item :label="`Reviewed (${mt[1].rows.length})`" header-class="text-subtitle1 text-weight-medium">
          <q-list separator>
            <q-item class="text-caption text-grey-6">
              <q-item-section style="max-width: 190px" />
              <q-item-section class="mt-col">Script (approved)</q-item-section>
              <q-item-section class="mt-col">SRT (captioned)</q-item-section>
              <q-item-section side style="width: 340px" />
              <q-item-section side style="width: 104px" />
            </q-item>
            <q-item v-for="d in mt[1].rows" :key="d.data!.id" class="text-grey-6">
              <q-item-section style="max-width: 190px">
                <router-link :to="`/topic/${d.topic}`">{{ d.topic }}</router-link>
                <q-item-label caption>slide {{ d.data!.slide }} · cue {{ d.data!.cue ?? '—' }}</q-item-label>
              </q-item-section>
              <q-item-section class="mt-col">
                <q-item-label>{{ d.data!.script }}</q-item-label>
              </q-item-section>
              <q-item-section class="mt-col">
                <q-item-label>{{ d.data!.srt }}</q-item-label>
              </q-item-section>
              <q-item-section side style="width: 340px">
                <div class="row items-center gap-sm">
                  <StateChip kind="ok" :label="d.data!.review!.decision === 'accept' ? 'kept SRT' : `corrected to “${d.data!.review!.text}”`" />
                  <span class="text-caption">{{ d.data!.review!.by || '' }}</span>
                  <q-btn flat dense size="sm" no-caps label="Undo" @click="review(d, { clear: true })" />
                </div>
              </q-item-section>
              <q-item-section side style="width: 104px">
                <q-btn v-if="d.data!.time !== null && d.data!.time !== undefined" flat dense no-caps color="primary"
                  :icon="nowPlaying === d.data!.id ? 'stop' : 'play_arrow'" :label="nowPlaying === d.data!.id ? 'Stop' : fmtTime(d.data!.time)"
                  @click="playSegment(d)" />
              </q-item-section>
            </q-item>
          </q-list>
        </q-expansion-item>
      </q-card>
    </template>
  </q-page>
</template>

<style scoped>
/* Quasar's item-sections default to flex-grow with min-width: auto, so two equal-flex
   columns still drift apart by a few px depending on each row's text (the browser lets
   content's min-content size win over the equal split). Forcing min-width: 0 makes the
   two text columns always split the space exactly in half, so the columns after them
   (input/button, play) land in the same spot on every row. */
.mt-col {
  flex: 1 1 0%;
  min-width: 0;
}
.pick {
  cursor: pointer;
  border-radius: 4px;
  transition: background-color 0.15s;
}
.pick:hover {
  background-color: rgba(255, 255, 255, 0.08);
}
.correct-btn {
  height: 40px;
}
</style>
