<script setup lang="ts">
// Whole-tree batch handover, replacing what OneDrive sync used to do: export a module or
// unit as a single zip (any content file bcn recognises, not just topic.md), or import one
// back. One-shot, not a live two-way folder — bcn transfer figures out what each file is
// and where it belongs from its name, the same naming bcn sync always used.
import { computed, ref, shallowRef } from 'vue';
import type { BcnTransferEnvelope, JobSummary, TransferListResponse, TranslationItem } from '@beacon/shared';
import { api, fileUrl } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { confirm } from '@/composables/confirm';
import { fmtAgo, fmtBytes, plural } from '@/format';
import { useBeacon } from '@/stores/beacon';

const LABEL: Record<string, string> = { written: 'written', unchanged: 'already identical', exists_differs: 'exists and differs: not written',
  unrecognized: 'unrecognized name: not placed', refused: 'outside scope: not placed', would_write: 'would be written' };
const good = (action: string) => ['written', 'unchanged', 'would_write'].includes(action);
const diffClass = (l: string) => (l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : '');
// "KV7016/U01/T01/topic.md" -> "KV7016-U01-T01", or null for anything not a topic file
// (course-map.md, activity.md, ...), which has no topic page to link to.
const TOPIC_PATH = /^([A-Z]{2}\d{4})\/(U\d{2})\/(T\d{2})\/topic(?:\.zh)?\.md$/;
function topicIdOf(path: string | null | undefined): string | null {
  const m = path ? TOPIC_PATH.exec(path) : null;
  return m ? `${m[1]}-${m[2]}-${m[3]}` : null;
}

const beacon = useBeacon();
const scope = ref('');
const media = ref(false);
const nested = ref(false);
const replace = ref(false);
const lists = shallowRef<TransferListResponse>({ exports: [] });
const lastImport = shallowRef<{ item: TranslationItem; env: BcnTransferEnvelope | undefined } | null>(null);
const busy = ref(false);
const over = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const fullBusy = ref(false);
const fullOver = ref(false);
const fullFileInput = ref<HTMLInputElement | null>(null);

async function refresh() {
  try { lists.value = await api<TransferListResponse>('/api/transfer'); } catch (e) { beacon.toast((e as Error).message, true); }
}
void refresh();

async function doExport() {
  if (!scope.value) return;
  busy.value = true;
  try {
    const job = await beacon.runJob('transfer', [scope.value], { export: true, media: media.value, nested: nested.value });
    const done = await beacon.awaitJob(job.id);
    const env = done.envelopes?.[0];
    if (env && !env.ok) beacon.toast(env.diagnostics.filter((d) => d.level === 'error').map((d) => d.message).join(' ') || 'Export failed.', true);
  } finally {
    busy.value = false;
    void refresh();
  }
}

/** Overwriting content that already differs is a real content-loss risk if clicked
 *  without thinking, so confirm before any import done with Replace ticked. */
async function confirmReplaceIfNeeded(): Promise<boolean> {
  if (!replace.value) return true;
  return (await confirm({
    title: 'Replace files that already exist and differ?',
    lines: ['Anything already on disk with different content will be overwritten. This cannot be undone.'],
    ok: 'Replace',
    danger: true,
  })) === true;
}

async function upload(file: File | undefined) {
  if (!file) return;
  if (!(await confirmReplaceIfNeeded())) return;
  try {
    const { path } = await api<{ path: string }>(`/api/transfer/upload?name=${encodeURIComponent(file.name)}`, { raw: file });
    await doImport({ name: file.name, path, kind: 'zip', bytes: file.size, mtime: new Date().toISOString() });
  } catch (e) { beacon.toast((e as Error).message, true); }
}
function onDrop(e: DragEvent) { over.value = false; void upload(e.dataTransfer?.files[0]); }

async function doImport(item: TranslationItem) {
  busy.value = true;
  try {
    const job = await api<JobSummary>('/api/transfer/import', { body: { source: item.path, replace: replace.value } });
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    lastImport.value = { item, env: done.envelopes?.[0] as unknown as BcnTransferEnvelope | undefined };
  } catch (e) { beacon.toast((e as Error).message, true); }
  busy.value = false;
}

// Full backup: everything bcn transfer --full covers (every module, media, programme.toml,
// the custom theme) in one zip. A separate flow from the scoped export/import above —
// infrequent, can be large and slow, and is for disaster recovery, not routine handover.
async function exportFull() {
  fullBusy.value = true;
  try {
    const job = await beacon.runJob('transfer', ['.'], { export: true, full: true });
    const done = await beacon.awaitJob(job.id);
    const env = done.envelopes?.[0];
    if (env && !env.ok) beacon.toast(env.diagnostics.filter((d) => d.level === 'error').map((d) => d.message).join(' ') || 'Full backup failed.', true);
    else beacon.toast('Full backup ready: see the list below.');
  } finally {
    fullBusy.value = false;
    void refresh();
  }
}

async function uploadFull(file: File | undefined) {
  if (!file) return;
  fullBusy.value = true;
  try {
    const { path } = await api<{ path: string }>(`/api/transfer/upload?name=${encodeURIComponent(file.name)}`, { raw: await file.arrayBuffer() });
    const job = await api<JobSummary>('/api/transfer/import', { body: { source: path, full: true } });
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    lastImport.value = { item: { name: file.name, path, kind: 'zip', bytes: file.size, mtime: new Date().toISOString() },
      env: done.envelopes?.[0] as unknown as BcnTransferEnvelope | undefined };
  } catch (e) { beacon.toast((e as Error).message, true); }
  fullBusy.value = false;
}
function onDropFull(e: DragEvent) { fullOver.value = false; void uploadFull(e.dataTransfer?.files[0]); }

const scopes = computed(() => Object.entries(beacon.status?.summary.modules || {}).sort()
  .flatMap(([m, info]) => [m, ...info.units.map((u) => `${m}/${u}`)]));
const isFull = (name: string) => name.includes('-programme-full');
const zips = computed(() => lists.value.exports.filter((x) => x.kind === 'zip' && !isFull(x.name)));
const fullZips = computed(() => lists.value.exports.filter((x) => x.kind === 'zip' && isFull(x.name)));
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Transfer" sub="Export a module or unit as a single zip — every file bcn recognises, not just scripts — to hand to a content creator or partner. Import one back: each file is placed by its name. By default nothing that already differs is overwritten; tick Replace to change that." />
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6 q-gutter-y-md">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Export</q-card-section>
          <q-card-section class="row items-center gap-sm">
            <q-select v-model="scope" dense outlined :options="scopes" label="Module or unit" style="min-width: 220px" />
            <q-btn unelevated color="primary" no-caps label="Export batch" :disable="!scope || busy" :loading="busy" @click="doExport" />
          </q-card-section>
          <q-card-section class="row items-center gap-md q-pt-none">
            <q-toggle v-model="media" dense label="Include images, video and audio">
              <q-tooltip max-width="320px">Without this, only text files are exported — a topic's assets/ images are not included, so an image it references won't be there to re-import later.</q-tooltip>
            </q-toggle>
            <q-toggle v-model="nested" dense label="Nested folder layout" />
          </q-card-section>
          <q-card-section class="text-caption text-grey-7 q-pt-none">
            {{ media ? 'Everything in scope, including topic images, the edited video and subtitles.' : 'Text files only: scripts, quizzes, the module map and the like — small and quick, but leaves out images.' }}
            {{ nested ? ' Files keep the pipeline\'s own U01/T01/topic.md layout.' : ' Files use the flat KV7016-U01-T01.md names.' }}
          </q-card-section>
        </q-card>
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Exports</q-card-section>
          <q-list v-if="zips.length" separator>
            <q-item v-for="x in zips" :key="x.path">
              <q-item-section><q-item-label class="text-mono">{{ x.name }}</q-item-label>
                <q-item-label caption>{{ fmtAgo(x.mtime, beacon.now) }} · {{ fmtBytes(x.bytes) }}</q-item-label></q-item-section>
              <q-item-section side><q-btn outline dense size="sm" no-caps icon="download" label="Download" :href="fileUrl(x.path, null, 'download=1')" /></q-item-section>
            </q-item>
          </q-list>
          <q-card-section v-else class="text-grey-7">No exports yet.</q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-md-6 q-gutter-y-md">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Import</q-card-section>
          <q-card-section>
            <div :class="['dropzone q-pa-lg text-center rounded-borders cursor-pointer', { 'bg-blue-1 text-black': over }]"
              style="border: 2px dashed var(--line)" @click="fileInput?.click()" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop">
              <q-icon name="upload_file" size="md" class="q-mb-sm" /><br>
              Drop a batch .zip here, or click to choose.
              <input ref="fileInput" type="file" accept=".zip" class="hidden" @change="upload(($event.target as HTMLInputElement).files?.[0])">
            </div>
          </q-card-section>
          <q-card-section class="row items-center q-pt-none">
            <q-checkbox v-model="replace" dense label="Replace files that already exist and differ">
              <q-tooltip max-width="320px">Normally a file already on disk with different content is left alone and the diff is shown instead. Tick this to overwrite it anyway, e.g. re-importing corrected content.</q-tooltip>
            </q-checkbox>
          </q-card-section>
          <q-card-section class="text-caption text-grey-7 q-pt-none">
            Any file bcn recognises by name — topic.md, activity.md, course-map.md, assignment-N.md, a topic's assets/ images, and so on — in
            flat or nested naming. A name it doesn't recognise is reported, not guessed at.
          </q-card-section>
        </q-card>
        <q-card v-if="lastImport?.env" flat bordered>
          <q-card-section class="row items-center gap-sm">
            <div class="text-subtitle1 text-weight-medium">Imported {{ lastImport.item.name }}</div>
            <StateChip :kind="lastImport.env.ok ? 'ok' : 'blocked'" :label="lastImport.env.ok ? 'all placed' : 'problems found'" />
          </q-card-section>
          <q-list separator>
            <q-item v-for="r in lastImport.env.results" :key="r.topic" class="column items-stretch">
              <div class="row items-center gap-sm">
                <StateChip :kind="good(r.action as string) ? 'ok' : 'blocked'" :label="LABEL[r.action as string] || (r.action as string)" />
                <router-link v-if="topicIdOf(r.path)" :to="`/topic/${topicIdOf(r.path)}`">{{ r.path }}</router-link>
                <span v-else-if="r.path" class="text-mono">{{ r.path }}</span>
                <span v-else class="text-mono text-grey-7">{{ r.topic }}</span>
              </div>
              <div v-if="r.diff" class="diff q-mt-sm"><div v-for="(l, i) in (r.diff as string).split('\n')" :key="i" :class="diffClass(l)">{{ l || ' ' }}</div></div>
            </q-item>
          </q-list>
          <q-card-section v-if="lastImport.env.validated?.length" class="text-caption text-grey-7">
            Validated: {{ (lastImport.env.validated as Array<{ topic: string; ok: boolean }>).filter((v) => !v.ok).length }} of {{ lastImport.env.validated.length }} topics have problems — see the Diagnostics page.
          </q-card-section>
        </q-card>
      </div>
    </div>

    <q-separator class="q-my-lg" />

    <div class="text-subtitle1 text-weight-medium q-mb-sm">Full backup</div>
    <p class="text-caption text-grey-7" style="max-width: 760px">
      Every module, its media, programme.toml and the custom theme, in one zip — enough to rebuild the whole programme from nothing. This is for
      disaster recovery, not routine handover: it can take a long time and produce a very large file. Use Export above for day-to-day batches.
    </p>
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6 q-gutter-y-md">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Export everything</q-card-section>
          <q-card-section>
            <q-btn unelevated color="primary" no-caps label="Export everything" :disable="fullBusy" :loading="fullBusy" @click="exportFull" />
          </q-card-section>
        </q-card>
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Backups</q-card-section>
          <q-list v-if="fullZips.length" separator>
            <q-item v-for="x in fullZips" :key="x.path">
              <q-item-section><q-item-label class="text-mono">{{ x.name }}</q-item-label>
                <q-item-label caption>{{ fmtAgo(x.mtime, beacon.now) }} · {{ fmtBytes(x.bytes) }}</q-item-label></q-item-section>
              <q-item-section side><q-btn outline dense size="sm" no-caps icon="download" label="Download" :href="fileUrl(x.path, null, 'download=1')" /></q-item-section>
            </q-item>
          </q-list>
          <q-card-section v-else class="text-grey-7">No full backups yet.</q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-md-6 q-gutter-y-md">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Restore from a full backup</q-card-section>
          <q-card-section>
            <div :class="['dropzone q-pa-lg text-center rounded-borders cursor-pointer', { 'bg-blue-1 text-black': fullOver }]"
              style="border: 2px dashed var(--line)" @click="fullFileInput?.click()" @dragover.prevent="fullOver = true" @dragleave="fullOver = false" @drop.prevent="onDropFull">
              <q-icon name="upload_file" size="md" class="q-mb-sm" /><br>
              Drop a full backup .zip here, or click to choose.
              <input ref="fullFileInput" type="file" accept=".zip" class="hidden" @change="uploadFull(($event.target as HTMLInputElement).files?.[0])">
            </div>
          </q-card-section>
          <q-card-section class="text-caption text-grey-7">
            Restores onto the existing programme — programme.toml and anything else that already differs is reported, not overwritten, same as any other import.
          </q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>
