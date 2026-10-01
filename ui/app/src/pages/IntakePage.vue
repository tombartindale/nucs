<script setup lang="ts">
// Intake: paste what Claude produced, and bcn identifies, checks, places and validates it.
// The only place the UI writes content, and it writes nothing itself: bcn intake does.
import { computed, ref, shallowRef, watch } from 'vue';
import type { BcnIntakeEnvelope, Diagnostic, JobSummary } from '@beacon/shared';
import { api } from '@/api';
import DiagnosticItem from '@/components/DiagnosticItem.vue';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { useBeacon } from '@/stores/beacon';

const LABEL: Record<string, string> = { written: 'written', unchanged: 'already identical', exists_differs: 'exists and differs: not written',
  refused: 'refused: not in course map', would_write: 'would be written' };
const DRAFT = 'intake-draft';
const beacon = useBeacon();

const read = () => { try { return sessionStorage.getItem(DRAFT) || ''; } catch { return ''; } };
const text = ref(read());
const path = ref('.');
const busy = ref(false);
const over = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const result = shallowRef<{ env: BcnIntakeEnvelope | undefined; dryRun: boolean } | null>(null);
const error = ref<string | null>(null);
watch(text, (t) => { try { sessionStorage.setItem(DRAFT, t); } catch { /* private window: fine */ } });

const moduleOptions = computed(() => [{ label: 'any module', value: '.' }, ...Object.keys(beacon.status?.summary.modules || {}).sort().map((m) => ({ label: m, value: m }))]);
const env = computed(() => result.value?.env);

async function runJob(job: JobSummary, dryRun: boolean): Promise<void> {
  beacon.trackJob(job);
  const done = await beacon.awaitJob(job.id);
  const e = done.envelopes?.[0] as unknown as BcnIntakeEnvelope | undefined;
  result.value = { env: e, dryRun };
  if (!e) error.value = 'bcn intake produced no result; see the job log.';
  else if (!dryRun && e.results.every((r) => ['written', 'unchanged'].includes(r.action as string))) {
    try { sessionStorage.removeItem(DRAFT); } catch { /* fine */ }
  }
}

async function submit(dryRun: boolean) {
  busy.value = true; error.value = null; result.value = null;
  try {
    const job = await api<JobSummary>('/api/intake', { body: { text: text.value, dry_run: dryRun, path: path.value } });
    await runJob(job, dryRun);
  } catch (e) { error.value = (e as Error).message; }
  busy.value = false;
}

// A batch of real topic.md files, e.g. from a content creator — a .zip, not pasted text.
async function submitFile(file: File | undefined, dryRun: boolean) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.zip')) { error.value = 'Choose a .zip file.'; return; }
  busy.value = true; error.value = null; result.value = null;
  try {
    const q = `name=${encodeURIComponent(file.name)}&path=${encodeURIComponent(path.value)}&dry_run=${dryRun ? '1' : '0'}`;
    const job = await api<JobSummary>(`/api/intake/upload?${q}`, { raw: await file.arrayBuffer() });
    await runJob(job, dryRun);
  } catch (e) { error.value = (e as Error).message; }
  busy.value = false;
}
function onFilePicked(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  void submitFile(file, false);
  (e.target as HTMLInputElement).value = '';
}
function onDrop(e: DragEvent) { over.value = false; void submitFile(e.dataTransfer?.files[0], false); }

const errsFor = (topic: string) => ((env.value?.diagnostics || []) as Diagnostic[]).filter((d) => d.topic === topic && !d.code.startsWith('INTAKE_'));
const diffClass = (l: string) => (l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : l.startsWith('@@') ? 'hunk' : '');
const good = (action: unknown) => ['written', 'unchanged', 'would_write'].includes(action as string);
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Intake" sub="Paste, and bcn identifies each topic by its front matter, checks it against the course map, places it, and validates it. It never overwrites a file that differs; it shows the diff instead." />
    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-6">
        <q-card flat bordered>
          <q-card-section>
            <q-input v-model="text" type="textarea" outlined input-style="height: 58vh; font-family: var(--mono); font-size: 12.5px"
              placeholder="Paste one topic or a whole unit, front matter included. Code fences are fine." />
          </q-card-section>
          <q-card-actions class="q-px-md q-pb-md">
            <span class="text-caption text-grey-7 q-mr-sm">Restrict to</span>
            <q-select v-model="path" dense outlined emit-value map-options :options="moduleOptions" style="min-width: 150px" />
            <q-space />
            <q-btn outline no-caps label="Check only" :disable="busy || !text.trim()" @click="submit(true)" />
            <q-btn unelevated color="primary" no-caps :loading="busy" label="Place and validate" :disable="busy || !text.trim()" @click="submit(false)" />
          </q-card-actions>
        </q-card>
        <q-card flat bordered class="q-mt-md">
          <q-card-section>
            <div :class="['dropzone q-pa-md text-center rounded-borders cursor-pointer', { 'bg-blue-1 text-black': over }]"
              style="border: 2px dashed var(--line)" @click="fileInput?.click()" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="onDrop">
              <q-icon name="upload_file" size="sm" class="q-mb-xs" /><br>
              Or drop a <strong>.zip</strong> of topic.md files here, e.g. a batch from a content creator — or click to choose.
              <div class="text-caption text-grey-7 q-mt-xs">Each file keeps its own topic_id front matter; multiple topics in one zip are fine.</div>
              <input ref="fileInput" type="file" accept=".zip" class="hidden" @change="onFilePicked">
            </div>
          </q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-md-6">
        <q-banner v-if="error" class="bg-negative text-white q-mb-md" rounded>{{ error }}</q-banner>
        <q-card v-if="env" flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium">
            {{ result!.dryRun ? 'Check' : 'Result' }}: {{ env.topics_found ?? env.results.length }} topic{{ env.results.length === 1 ? '' : 's' }} found
          </q-card-section>
          <q-list separator>
            <q-item v-for="r in env.results" :key="r.topic" class="column items-stretch">
              <div class="row items-center gap-sm">
                <StateChip :kind="good(r.action) ? 'ok' : 'blocked'" :label="LABEL[r.action as string] || (r.action as string) || 'failed'" />
                <router-link v-if="r.path" :to="`/topic/${r.topic}`">{{ r.topic }}</router-link><strong v-else>{{ r.topic }}</strong>
                <StateChip v-if="r.validate_ok !== undefined && r.validate_ok !== null" :kind="r.validate_ok ? 'ok' : 'error'" :label="r.validate_ok ? 'validates' : 'validation errors'" />
              </div>
              <q-list v-if="errsFor(r.topic).length" dense>
                <DiagnosticItem v-for="(d, i) in errsFor(r.topic)" :key="i" :d="d" />
              </q-list>
              <div v-if="r.diff" class="diff q-mt-sm"><div v-for="(l, i) in (r.diff as string).split('\n')" :key="i" :class="diffClass(l)">{{ l || ' ' }}</div></div>
            </q-item>
          </q-list>
        </q-card>
        <q-card v-else flat bordered><q-card-section class="text-grey-7">Results appear here.</q-card-section></q-card>
      </div>
    </div>
  </q-page>
</template>
