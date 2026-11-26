<script setup lang="ts">
// Every topic in the programme, one row each, with a drop target for each file the editor and
// the translator send back: the edit video and subtitles, the translated subtitles and slides.
// A file dropped (or chosen) in a cell is placed for that row's topic whatever it is called,
// so a translator's own naming never matters. Each target is the real file input laid over
// the cell, so clicking opens the native picker directly and dropping onto it just works.
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import type { StatusArtifact, TopicStatus } from '@beacon/shared';
import { confirm } from '@/composables/confirm';
import { type Abortable, type UploadKind, type UploadKindSpec, UPLOAD_KINDS, uploadTopicFile } from '@/composables/topicUpload';
import { fmtAgo } from '@/format';
import { useBeacon } from '@/stores/beacon';

const beacon = useBeacon();
if (!beacon.status) void beacon.loadStatus().catch((e: Error) => beacon.toast(e.message, true));

const module = ref<string | null>(null);
const search = ref('');
const missingOnly = ref(false);

type Cell = `${string}:${UploadKind}`;
const busy = reactive<Record<Cell, boolean>>({});
const sent = reactive<Record<Cell, number>>({});   // fraction of bytes sent; absent while the server places it
const over = reactive<Record<Cell, boolean>>({});
const inFlight: Record<Cell, Abortable> = {};
const anyBusy = computed(() => Object.values(busy).some(Boolean));
const cell = (id: string, kind: UploadKind): Cell => `${id}:${kind}`;

const topics = computed<TopicStatus[]>(() => beacon.status?.results ?? []);
const modules = computed(() => [...new Set(topics.value.map((t) => t.module))].sort());
const moduleTitle = (m: string) => beacon.status?.summary.modules[m]?.title ?? '';

function file(t: TopicStatus, k: UploadKindSpec): StatusArtifact | undefined {
  return t.artifacts.find((a) => a.key === k.key && a.exists);
}

const rows = computed(() => {
  const q = search.value.trim().toLowerCase();
  return topics.value.filter((t) => (!module.value || t.module === module.value)
    && (!q || t.topic.toLowerCase().includes(q) || t.title.toLowerCase().includes(q))
    && (!missingOnly.value || UPLOAD_KINDS.some((k) => !file(t, k))));
});
/** Rows with a module heading before the first topic of each module. */
const grouped = computed(() => {
  const out: Array<{ module: string; topics: TopicStatus[] }> = [];
  for (const t of rows.value) {
    if (out.at(-1)?.module !== t.module) out.push({ module: t.module, topics: [] });
    out.at(-1)!.topics.push(t);
  }
  return out;
});
const counts = computed(() => UPLOAD_KINDS.map((k) => rows.value.filter((t) => file(t, k)).length));

function extOk(k: UploadKindSpec, f: File): boolean {
  const ext = f.name.toLowerCase().split('.').pop() ?? '';
  return k.accept.split(',').some((a) => a.startsWith('.') && a.slice(1) === ext);
}

async function onPick(t: TopicStatus, k: UploadKindSpec, e: Event) {
  const input = e.target as HTMLInputElement;
  const f = input.files?.[0];
  input.value = '';   // so choosing the same file again still fires change
  over[cell(t.topic, k.kind)] = false;
  if (f) await upload(t, k, f);
}

async function upload(t: TopicStatus, k: UploadKindSpec, f: File) {
  const c = cell(t.topic, k.kind);
  if (!extOk(k, f)) { beacon.toast(`${f.name} is not a ${k.accept.split(',')[0]} file, so it was not uploaded as the ${k.short.toLowerCase()}.`, true); return; }
  const replacing = !!file(t, k);
  if (replacing && !(await confirm({
    title: `Replace the ${k.short.toLowerCase()} for ${t.topic}?`,
    lines: [
      `This replaces ${k.path} with ${f.name}.`,
      ...(k.kind === 'master' ? ['A re-cut moves the cues: run cues again once it is in.'] : []),
    ],
    ok: 'Replace',
    danger: true,
  }))) return;

  busy[c] = true;
  sent[c] = 0;
  try {
    const job = await uploadTopicFile(t.topic, k.kind, f, replacing, (a) => { inFlight[c] = a; }, (x) => { sent[c] = x; });
    delete sent[c];
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    await beacon.loadStatus().catch(() => undefined);
    // Placed is what matters here: a translation that is in but fails its checks still lands,
    // and its problems show on the topic and in Diagnostics.
    const diags = done.envelopes?.flatMap((env) => env.diagnostics) ?? [];
    const placed = diags.some((d) => d.code === 'XL_IMPORTED') || done.state === 'done';
    if (!placed) beacon.toast(`The ${k.short.toLowerCase()} for ${t.topic} was not placed; see Jobs.`, true);
    else if (done.state !== 'done') beacon.toast(`${k.short} placed for ${t.topic}, but its checks found problems; see the topic.`, true);
    else beacon.toast(`${k.short} placed for ${t.topic}.`);
  } catch (e) {
    beacon.toast((e as Error).message, true);
  } finally {
    delete busy[c];
    delete sent[c];
    delete inFlight[c];
  }
}

function onUnload(e: BeforeUnloadEvent) { if (anyBusy.value) { e.preventDefault(); e.returnValue = ''; } }
onMounted(() => window.addEventListener('beforeunload', onUnload));
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onUnload);
  for (const a of Object.values(inFlight)) a.abort();
});
onBeforeRouteLeave(async () => {
  if (!anyBusy.value) return true;
  return (await confirm({
    title: 'Uploads are still in progress',
    lines: ['Leaving now cancels them; those files will need to be uploaded again.'],
    ok: 'Leave and cancel uploads',
    danger: true,
  })) === true;
});
</script>

<template>
  <div>
    <div class="row items-center gap-sm q-mb-md">
      <q-select v-model="module" dense outlined clearable emit-value map-options style="min-width: 220px" label="Module"
        :options="modules.map((m) => ({ label: moduleTitle(m) ? `${m} · ${moduleTitle(m)}` : m, value: m }))" />
      <q-input v-model="search" dense outlined clearable debounce="150" label="Find a topic" style="min-width: 220px">
        <template #prepend><q-icon name="search" /></template>
      </q-input>
      <q-toggle v-model="missingOnly" dense label="Only topics still missing a file" />
      <q-space />
      <div class="text-caption text-grey-7">{{ rows.length }} topics</div>
    </div>

    <div v-if="!beacon.status" class="row justify-center q-pa-xl"><q-spinner size="32px" color="primary" /></div>
    <div v-else-if="!rows.length" class="text-grey-7 q-pa-md">No topics match.</div>
    <div v-else class="grid-wrap">
      <table class="files-grid">
        <thead>
          <tr>
            <th class="text-left">Topic</th>
            <th v-for="(k, i) in UPLOAD_KINDS" :key="k.kind">
              <q-icon :name="k.icon" size="18px" class="q-mr-xs" />{{ k.short }}
              <div class="text-caption text-grey-7 text-weight-regular">{{ counts[i] }} of {{ rows.length }} in</div>
            </th>
          </tr>
        </thead>
        <tbody v-for="g in grouped" :key="g.module">
          <tr class="module-row"><td :colspan="UPLOAD_KINDS.length + 1">
            <router-link :to="`/module/${g.module}`" class="text-weight-medium">{{ g.module }}</router-link>
            <span class="text-grey-7 q-ml-sm">{{ moduleTitle(g.module) }}</span>
          </td></tr>
          <tr v-for="t in g.topics" :key="t.topic">
            <td class="topic-cell">
              <router-link :to="`/topic/${t.topic}`" class="text-mono">{{ t.topic }}</router-link>
              <div class="text-caption text-grey-7 ellipsis">{{ t.title }}</div>
            </td>
            <td v-for="k in UPLOAD_KINDS" :key="k.kind" class="drop-cell">
              <div :class="['drop', { present: !!file(t, k), over: over[cell(t.topic, k.kind)], disabled: !t.has_dir }]">
                <template v-if="busy[cell(t.topic, k.kind)]">
                  <template v-if="sent[cell(t.topic, k.kind)] !== undefined">
                    <q-linear-progress :value="sent[cell(t.topic, k.kind)]" size="6px" rounded color="primary" class="full-width" />
                    <div class="text-caption">{{ Math.round((sent[cell(t.topic, k.kind)] ?? 0) * 100) }}%</div>
                  </template>
                  <div v-else class="row items-center no-wrap gap-xs text-caption"><q-spinner size="14px" color="primary" />Placing…</div>
                </template>
                <template v-else-if="!t.has_dir">
                  <span class="text-caption text-grey-6">No folder yet</span>
                </template>
                <template v-else-if="file(t, k)">
                  <div class="row items-center no-wrap gap-xs">
                    <q-icon name="check_circle" color="positive" size="16px" />
                    <span class="text-caption">{{ fmtAgo(file(t, k)!.mtime, beacon.now) }}</span>
                  </div>
                  <div class="text-caption text-grey-7 hint">Drop to replace</div>
                </template>
                <template v-else>
                  <q-icon name="upload_file" size="18px" class="text-grey-6" />
                  <div class="text-caption text-grey-7">Drop {{ k.accept.split(',')[0] }}</div>
                </template>
                <input v-if="t.has_dir && !busy[cell(t.topic, k.kind)]" type="file" class="drop-input" :accept="k.accept"
                  :aria-label="`${file(t, k) ? 'Replace' : 'Upload'} ${k.short.toLowerCase()} for ${t.topic}`"
                  :title="file(t, k) ? `${k.path} is in. Drop or click to replace it.` : `Drop or click to upload ${k.path}`"
                  @dragenter="over[cell(t.topic, k.kind)] = true" @dragleave="over[cell(t.topic, k.kind)] = false"
                  @drop="over[cell(t.topic, k.kind)] = false" @change="onPick(t, k, $event)">
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.grid-wrap { overflow-x: auto; }
.files-grid { width: 100%; border-collapse: separate; border-spacing: 6px 4px; min-width: 760px; }
.files-grid th { font-weight: 500; text-align: center; padding: 4px; white-space: nowrap; }
.topic-cell { max-width: 280px; padding-right: 8px; }
.module-row td { padding: 14px 0 2px; border-bottom: 1px solid var(--line); }
.drop-cell { width: 17%; }
.drop {
  position: relative; min-height: 52px; padding: 4px 6px; border-radius: 6px; border: 2px dashed var(--line);
  display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center;
  transition: background-color 0.1s, border-color 0.1s;
}
.drop.present { border-style: solid; border-color: transparent; background: rgba(33, 186, 69, 0.08); }
.drop.present .hint { visibility: hidden; }
.drop:hover .hint, .drop.over .hint { visibility: visible; }
.drop:hover:not(.disabled), .drop.over { border-color: var(--q-primary); border-style: dashed; }
.drop.over { background: rgba(25, 118, 210, 0.12); }
.drop.disabled { opacity: 0.6; }
/* The real file input, invisible, over the whole cell: a click opens the picker, a drop lands on it. */
.drop-input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; }
</style>
