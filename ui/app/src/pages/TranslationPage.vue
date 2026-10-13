<script setup lang="ts">
// Translation round trip: a batch handover out, and a batch check on the way back,
// so everything wrong goes back to the translator in one message.
import { computed, ref, shallowRef } from 'vue';
import type { BcnTranslationEnvelope, JobSummary, TranslationItem, TranslationListResponse } from '@beacon/shared';
import { api, fileUrl } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { fmtAgo, fmtBytes, plural } from '@/format';
import { useBeacon } from '@/stores/beacon';

type Env = BcnTranslationEnvelope & { return_to_translator?: Array<{ topic: string; message: string }> };
// A loose file the translator sent back one at a time, under a name that doesn't match
// <topic_id>.zh.md / .zh.srt — staged here so its real destination can be picked before
// it's written to disk, instead of being silently ignored by bcn translation --import.
interface PendingFile { id: number; file: File; topic: string | null; kind: 'md' | 'srt'; custom: boolean; customName: string }
const beacon = useBeacon();
const scope = ref('');
const lists = shallowRef<TranslationListResponse>({ exports: [], returned: [] });
const lastImport = shallowRef<{ item: TranslationItem; env: Env | undefined } | null>(null);
const busy = ref(false);
const over = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const pending = ref<PendingFile[]>([]);
const pendingBusy = ref(false);
let nextPendingId = 0;

async function refresh() {
  try { lists.value = await api<TranslationListResponse>('/api/translation'); } catch (e) { beacon.toast((e as Error).message, true); }
}
void refresh();

async function doExport() {
  if (!scope.value) return;
  busy.value = true;
  try {
    const job = await beacon.runJob('translation', [scope.value], { export: true });
    const done = await beacon.awaitJob(job.id);
    const env = done.envelopes?.[0];
    if (env && !env.ok) beacon.toast(env.diagnostics.filter((d) => d.level === 'error').map((d) => d.message).join(' ') || 'Export failed.', true);
  } finally {
    busy.value = false;
    void refresh();
  }
}

async function upload(file: File | undefined) {
  if (!file) return;
  try {
    await api(`/api/translation/upload?name=${encodeURIComponent(file.name)}`, { raw: await file.arrayBuffer() });
    beacon.toast(`Uploaded ${file.name}`);
  } catch (e) { beacon.toast((e as Error).message, true); }
  void refresh();
}
// A .zip still goes straight in, same as before. Anything else is a loose file with a
// name bcn translation --import won't recognise, so it's staged for renaming instead.
function addFiles(files: Iterable<File>) {
  for (const f of files) {
    if (f.name.toLowerCase().endsWith('.zip')) { void upload(f); continue; }
    pending.value.push({
      id: nextPendingId++, file: f, topic: null,
      kind: f.name.toLowerCase().endsWith('.srt') ? 'srt' : 'md',
      custom: false, customName: f.name,
    });
  }
}
function onDrop(e: DragEvent) { over.value = false; if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files); }
function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files?.length) addFiles(input.files);
  input.value = '';
}
function removePending(id: number) { pending.value = pending.value.filter((p) => p.id !== id); }
function targetName(p: PendingFile): string | null {
  return p.custom ? (p.customName.trim() || null) : (p.topic ? `${p.topic}.zh.${p.kind}` : null);
}
const topicOptions = computed(() => [...(beacon.status?.results || [])]
  .map((r) => ({ label: r.title ? `${r.topic} — ${r.title}` : r.topic, value: r.topic }))
  .sort((a, b) => a.value.localeCompare(b.value)));
const pendingReady = computed(() => pending.value.length > 0 && pending.value.every((p) => !!targetName(p)));
async function uploadPending() {
  if (!pendingReady.value) return;
  const batch = `manual-${Date.now()}`;
  pendingBusy.value = true;
  try {
    for (const p of pending.value) {
      const name = targetName(p);
      if (!name) continue;
      await api(`/api/translation/upload?name=${encodeURIComponent(name)}&batch=${encodeURIComponent(batch)}`, { raw: await p.file.arrayBuffer() });
    }
    beacon.toast(`Uploaded ${plural(pending.value.length, 'file')}`);
    pending.value = [];
  } catch (e) { beacon.toast((e as Error).message, true); }
  pendingBusy.value = false;
  void refresh();
}

async function doImport(item: TranslationItem) {
  busy.value = true;
  try {
    const job = await api<JobSummary>('/api/translation/import', { body: { source: item.path } });
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    lastImport.value = { item, env: done.envelopes?.[0] as unknown as Env | undefined };
  } catch (e) { beacon.toast((e as Error).message, true); }
  busy.value = false;
}

const scopes = computed(() => Object.entries(beacon.status?.summary.modules || {}).sort()
  .flatMap(([m, info]) => [m, ...info.units.map((u) => `${m}/${u}`)]));
const ready = computed(() => (beacon.status?.results || [])
  .filter((r) => r.zh.next === 'translation_export' && (!scope.value || r.path.startsWith(scope.value))));
// Assignment briefs and unit activities ride along in the same batch (bcn translation),
// once they exist and have no outstanding validate error — mirrored from how translation.py
// itself decides a document is ready to export.
const readyDocs = computed(() => Object.values(beacon.status?.summary.modules || {})
  .flatMap((info) => info.documents || [])
  .filter((d) => /\/(assignment-\d+|activity)\.md$/.test(d.path) && d.exists && !d.errors
    && (!scope.value || d.path.startsWith(scope.value))));
const zips = computed(() => lists.value.exports.filter((x) => x.kind === 'zip'));

const message = computed(() => {
  const items = lastImport.value?.env?.return_to_translator || [];
  if (!items.length) return '';
  const byTopic = new Map<string, string[]>();
  for (const x of items) { if (!byTopic.has(x.topic)) byTopic.set(x.topic, []); byTopic.get(x.topic)!.push(x.message); }
  return ['The following returned files need correcting. Slide breaks must match the English exactly, and SRT timings must be identical cue for cue.', '',
    ...[...byTopic.entries()].flatMap(([t, ms]) => [`${t}:`, ...ms.map((m) => `  - ${m}`), ''])].join('\n');
});
const copy = () => navigator.clipboard.writeText(message.value).then(() => beacon.toast('Copied'));
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Translation" sub="Export sends the reviewed English SRT and narration-stripped slides. Import places the returned files and checks parity and timings across the batch." />
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6 q-gutter-y-md">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-none">Export</q-card-section>
          <q-card-section class="row items-center gap-sm">
            <q-select v-model="scope" dense outlined :options="scopes" label="Module or unit" style="min-width: 220px" />
            <q-btn unelevated color="primary" no-caps label="Export batch" :disable="!scope || busy" :loading="busy" @click="doExport" />
          </q-card-section>
          <q-card-section class="text-caption text-grey-7 q-pt-none">
            {{ plural(ready.length, 'topic') }}{{ readyDocs.length ? ` and ${plural(readyDocs.length, 'assessment document')}` : '' }}
            in {{ scope || 'the programme' }} ready to send.
            A batch goes all or nothing: everything in scope must have passed validate (and, for a topic, subtitles
            too, with its mis-transcriptions reviewed).
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
              Drop the returned .zip here, or individual .md/.srt files if the translator sent them loose
              (you'll pick the right topic for each before they're saved) — or click to choose, several at once.
              Or put a folder in translation/returned/.
              <input ref="fileInput" type="file" multiple accept=".zip,.md,.srt" class="hidden" @change="onFileInput">
            </div>
          </q-card-section>
          <q-card-section v-if="pending.length" class="q-gutter-y-sm">
            <div class="text-caption text-grey-7">
              These don't match &lt;topic_id&gt;.zh.md / .zh.srt, so pick the topic (and script or subtitles) each one is,
              or type the exact destination name, before uploading.
            </div>
            <div v-for="p in pending" :key="p.id" class="row items-center q-col-gutter-sm q-mb-xs">
              <div class="col-3 text-caption text-mono ellipsis" :title="p.file.name">{{ p.file.name }}</div>
              <template v-if="!p.custom">
                <q-select v-model="p.topic" dense outlined emit-value map-options :options="topicOptions"
                  label="Topic" class="col-5" style="min-width: 160px" />
                <q-btn-toggle v-model="p.kind" dense no-caps unelevated
                  :options="[{ label: '.zh.md', value: 'md' }, { label: '.zh.srt', value: 'srt' }]" class="col-auto" />
              </template>
              <q-input v-else v-model="p.customName" dense outlined label="Exact destination filename" class="col-8" />
              <q-space />
              <q-btn flat dense round size="sm" :icon="p.custom ? 'list' : 'edit'" @click="p.custom = !p.custom"
                :title="p.custom ? 'Pick a topic instead' : 'Type the exact filename instead'" />
              <q-btn flat dense round size="sm" icon="close" @click="removePending(p.id)" title="Remove" />
            </div>
            <div class="row items-center gap-sm">
              <q-btn unelevated color="primary" no-caps label="Upload files" :disable="!pendingReady || pendingBusy"
                :loading="pendingBusy" @click="uploadPending" />
              <q-btn flat no-caps label="Clear" :disable="pendingBusy" @click="pending = []" />
            </div>
          </q-card-section>
          <q-list v-if="lists.returned.length" separator>
            <q-item v-for="x in lists.returned" :key="x.path">
              <q-item-section><q-item-label class="text-mono">{{ x.name }}</q-item-label>
                <q-item-label caption>{{ x.kind }} · {{ fmtAgo(x.mtime, beacon.now) }}</q-item-label></q-item-section>
              <q-item-section side><q-btn unelevated dense size="sm" color="primary" no-caps label="Import and check" :disable="busy" @click="doImport(x)" /></q-item-section>
            </q-item>
          </q-list>
          <q-card-section v-else class="text-grey-7">Nothing returned yet.</q-card-section>
          <q-card-section class="text-caption text-grey-7">Expected names: &lt;topic_id&gt;.zh.md and &lt;topic_id&gt;.zh.srt, or
            &lt;module&gt;-assignment-N.zh.md / &lt;module&gt;-&lt;unit&gt;-activity.zh.md for a returned assessment document.</q-card-section>
        </q-card>
        <q-card v-if="lastImport?.env" flat bordered>
          <q-card-section class="row items-center gap-sm">
            <div class="text-subtitle1 text-weight-medium">Imported {{ lastImport.item.name }}</div>
            <StateChip :kind="lastImport.env.ok ? 'ok' : 'blocked'" :label="lastImport.env.ok ? 'all passed' : 'problems found'" />
          </q-card-section>
          <q-markup-table flat dense>
            <thead><tr><th class="text-left">Topic</th><th class="text-left">Files</th><th class="text-left">Parity</th><th class="text-left">SRT timings</th></tr></thead>
            <tbody>
              <tr v-for="x in lastImport.env.results" :key="x.topic">
                <td><router-link :to="`/topic/${x.topic}`">{{ x.topic }}</router-link></td>
                <td class="text-caption">{{ Object.entries(x.files || {}).map(([k, v]) => `${k}: ${v}`).join(', ') }}</td>
                <td v-for="(v, k) in [x.validate_ok, x.subtitles_ok]" :key="k">
                  <StateChip v-if="v !== undefined && v !== null" :kind="v ? 'ok' : 'blocked'" :label="v ? 'ok' : 'fail'" /><template v-else>—</template>
                </td>
              </tr>
            </tbody>
          </q-markup-table>
          <q-card-section v-if="message" class="q-gutter-y-sm">
            <div class="text-subtitle2">Back to the translator, in one message</div>
            <q-input :model-value="message" type="textarea" outlined readonly input-style="height: 200px" />
            <q-btn outline no-caps icon="content_copy" label="Copy message" @click="copy" />
          </q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>
