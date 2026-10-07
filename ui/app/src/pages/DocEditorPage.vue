<script setup lang="ts">
// Module document editor: course-map.md, a unit's activity.md, or assignment-N.md, in the
// browser, with the problems bcn finds shown beside it as you type. bcn docedit does all
// checking and writing: --dry-run to check, a job to save, validating the *whole* module so
// a cross-file effect (e.g. removing a learning outcome) shows up before you save. Saving is
// refused if the file changed on disk since it was loaded.
//
// Keys: ⌘/Ctrl-S save · ⌘/Ctrl-Enter check now · Tab indents.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue';
import type { BcnDoceditEnvelope, Diagnostic, JobSummary, ModDocSourceResponse } from '@beacon/shared';
import { api } from '@/api';
import DiagnosticItem from '@/components/DiagnosticItem.vue';
import PageHeader from '@/components/PageHeader.vue';
import { confirm } from '@/composables/confirm';
import { useLineGutter } from '@/composables/lineGutter';
import { LEVEL_ORDER, plural } from '@/format';
import { useBeacon } from '@/stores/beacon';

const CHECK_DELAY_MS = 700;

const props = defineProps<{ path: string; line: number | null }>();
const beacon = useBeacon();
const draftKey = `beacon-editdoc-${props.path}`;
const module = props.path.split('/')[0];
const docName = props.path.split('/').slice(1).join('/');
type Summary = BcnDoceditEnvelope['results'][number];

const base = shallowRef<ModDocSourceResponse>({ text: '', sha256: null, exists: false, path: props.path });  // what is on disk
const text = ref('');
const problems = shallowRef<Diagnostic[]>([]);
const summary = shallowRef<Summary | null>(null);
const checking = ref(false);
const saving = ref(false);
const loaded = ref(false);
const { textarea, measure, gutter, gutterLines, drawGutter, redrawSoon, jumpTo, observe } = useLineGutter(text, problems);
const fileInput = ref<HTMLInputElement | null>(null);
const dropOver = ref(false);
let checkSeq = 0;
let checkTimer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;

const dirty = computed(() => text.value !== base.value.text);
const shown = computed(() => problems.value.filter((p) => p.code !== 'EDIT_SAVED')
  .sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level] || (a.line || 0) - (b.line || 0)));
const status = computed(() => {
  const errs = problems.value.filter((p) => p.level === 'error').length;
  const warns = problems.value.filter((p) => p.level === 'warn').length;
  return [dirty.value ? 'Unsaved changes' : 'Saved', checking.value ? 'checking…' : null,
    summary.value ? `${plural(errs, 'error')} · ${plural(warns, 'warning')}` : null].filter(Boolean).join(' · ');
});

const qp = `path=${encodeURIComponent(props.path)}`;

async function check() {
  clearTimeout(checkTimer);
  const seq = ++checkSeq;
  checking.value = true;
  try {
    const env = await api<BcnDoceditEnvelope>(`/api/moddoc/check?${qp}`, { body: { text: text.value } });
    if (seq !== checkSeq || disposed) return;   // a newer check is on its way
    problems.value = env.diagnostics as Diagnostic[];
    summary.value = env.results?.[0] ?? null;
  } catch (e) {
    if (seq === checkSeq) beacon.toast(`Could not check: ${(e as Error).message}`, true);
  }
  if (seq === checkSeq) checking.value = false;
  drawGutter();
}

function onInput() {
  redrawSoon();
  clearTimeout(checkTimer);
  checkTimer = setTimeout(check, CHECK_DELAY_MS);
  try { sessionStorage.setItem(draftKey, JSON.stringify({ text: text.value, sha256: base.value.sha256 })); } catch { /* fine */ }
}

async function save(overwrite = false): Promise<void> {
  if (saving.value) return;
  saving.value = true;
  try {
    const job = await api<JobSummary>(`/api/moddoc/save?${qp}`, { body: { text: text.value, expect_sha: base.value.sha256, overwrite } });
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    const env = done.envelopes?.[0];
    if (env?.diagnostics.some((d) => d.code === 'EDIT_CONFLICT')) {
      saving.value = false;
      const ok = await confirm({
        title: 'Overwrite the newer version?', ok: 'Overwrite', danger: true,
        lines: [`${base.value.path} was changed on disk after you opened it (a sync pull or another editor).`,
          'Overwrite replaces that version with yours; the replaced version is kept in a .history folder next to it. Or cancel, copy what you need, and reload the page to see the new version.'],
      });
      if (ok) return save(true);
      return;
    }
    if (!env || done.state !== 'done') throw new Error('the save job failed; see Jobs');
    const r = (env.results?.[0] || {}) as { sha256?: string; written?: boolean };
    base.value = { ...base.value, text: text.value, sha256: r.sha256 || base.value.sha256, exists: true };
    try { sessionStorage.removeItem(draftKey); } catch { /* fine */ }
    problems.value = env.diagnostics;
    summary.value = (env.results?.[0] as Summary) ?? null;
    const errs = problems.value.filter((p) => p.level === 'error').length;
    beacon.toast(r.written === false ? 'No changes to save.' : `Saved${errs ? ` · ${plural(errs, 'problem')} left` : ' · no problems'}`);
  } catch (e) {
    beacon.toast(`Not saved: ${(e as Error).message}`, true);
  }
  saving.value = false;
  drawGutter();
}

// A colleague's file replaces the editor's contents for review, the same as if it had been
// pasted in — it still goes through check() and the existing dirty/save/conflict flow rather
// than writing straight to disk, so a bad or stale upload is caught before anything is overwritten.
async function uploadFile(file: File | undefined) {
  if (!file) return;
  if (!file.name.endsWith('.md') && !file.name.endsWith('.txt')) {
    beacon.toast('Choose a .md file.', true);
    return;
  }
  if (dirty.value && !(await confirm({ title: 'Replace your unsaved changes?', lines: [`${file.name} will replace the text currently in the editor.`], ok: 'Replace', danger: true }))) return;
  text.value = await file.text();
  onInput();
  await nextTick();
  drawGutter();
  void check();
  beacon.toast(`Loaded ${file.name} — review and Save to apply it.`);
}
function onFilePicked(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  void uploadFile(file);
  (e.target as HTMLInputElement).value = '';
}

async function revert() {
  if (dirty.value && !(await confirm({ title: 'Discard your changes?', lines: ['The editor goes back to the version on disk.'], ok: 'Discard', danger: true }))) return;
  text.value = base.value.text;
  try { sessionStorage.removeItem(draftKey); } catch { /* fine */ }
  await nextTick();
  drawGutter();
  void check();
}

function onTab(e: KeyboardEvent) {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  e.preventDefault();
  const ta = textarea.value!;
  ta.setRangeText('  ', ta.selectionStart, ta.selectionEnd, 'end');
  text.value = ta.value;
  onInput();
}
function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (dirty.value) void save(); }
  else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); void check(); }
}
function onUnload(e: BeforeUnloadEvent) { if (dirty.value) { e.preventDefault(); e.returnValue = ''; } }

onMounted(async () => {
  document.addEventListener('keydown', onKey);
  window.addEventListener('beforeunload', onUnload);
  try { base.value = await api<ModDocSourceResponse>(`/api/moddoc?${qp}`); } catch (e) { beacon.toast((e as Error).message, true); return; }
  if (disposed) return;
  let recovered: { text: string; sha256: string | null } | null = null;
  try { recovered = JSON.parse(sessionStorage.getItem(draftKey) || 'null'); } catch { /* fine */ }
  if (recovered && recovered.text !== base.value.text && recovered.sha256 === base.value.sha256) {
    text.value = recovered.text;
    beacon.toast('Recovered your unsaved changes from before.');
  } else {
    text.value = base.value.text;
  }
  loaded.value = true;
  await nextTick();
  observe();
  drawGutter();
  await check();
  if (props.line) jumpTo(props.line); else textarea.value?.focus();
});

onBeforeUnmount(() => {
  disposed = true;
  clearTimeout(checkTimer);
  document.removeEventListener('keydown', onKey);
  window.removeEventListener('beforeunload', onUnload);
  if (dirty.value) beacon.toast('Unsaved changes are kept in this tab: reopen the editor to carry on.');
});
</script>

<template>
  <q-page padding>
    <PageHeader :title="`Edit ${docName}`" :sub="status"
      :crumbs="[{ label: 'Programme', to: '/' }, { label: module, to: `/module/${module}` }, { label: docName, to: `/doc/${path}` }]">
      <q-btn outline no-caps label="Check now" @click="check"><q-tooltip>⌘/Ctrl-Enter</q-tooltip></q-btn>
      <q-btn outline no-caps label="Upload file…" @click="fileInput?.click()" />
      <input ref="fileInput" type="file" accept=".md,.txt" class="hidden" @change="onFilePicked">
      <q-btn outline no-caps label="Revert" @click="revert" />
      <q-btn unelevated color="primary" no-caps :label="saving ? 'Saving…' : 'Save'" :disable="saving || !dirty" @click="save()"><q-tooltip>⌘/Ctrl-S</q-tooltip></q-btn>
      <q-btn flat no-caps label="Back to document" :to="`/doc/${path}`" />
    </PageHeader>
    <div class="row q-col-gutter-md">
      <div class="col-12 col-lg-8">
        <q-card flat bordered>
          <div class="ed-box" :class="{ 'ed-drop-over': dropOver }"
            @dragover.prevent="dropOver = true" @dragleave.prevent="dropOver = false"
            @drop.prevent="dropOver = false; uploadFile($event.dataTransfer?.files[0])">
            <div ref="gutter" class="ed-gutter" aria-hidden="true">
              <div v-for="(g, i) in gutterLines" :key="i" :class="['ln', g.level]" :style="{ height: `${g.height}px` }">{{ i + 1 }}</div>
              <div :style="{ height: `${textarea?.clientHeight || 0}px` }"></div>
            </div>
            <textarea ref="textarea" v-model="text" class="ed-text" spellcheck="true" autocomplete="off" aria-label="Document text"
              :placeholder="loaded && !base.exists ? `${docName} does not exist yet. Paste or write it here, upload a file, or drop one here.` : ''"
              @input="onInput" @scroll="gutter && (gutter.scrollTop = textarea!.scrollTop)" @keydown.tab="onTab"></textarea>
            <div ref="measure" class="ed-measure" aria-hidden="true"></div>
            <div v-if="dropOver" class="ed-drop-hint">Drop to load {{ docName }}</div>
          </div>
          <q-card-section class="text-caption text-grey-7 q-py-sm">⌘S save · ⌘↵ check</q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-lg-4">
        <q-card flat bordered>
          <q-card-section class="text-subtitle1 text-weight-medium q-pb-sm">Problems</q-card-section>
          <q-list v-if="shown.length" separator>
            <DiagnosticItem v-for="(p, i) in shown" :key="i" :d="p">
              <template #message>
                <a v-if="p.line && p.file === path" href="#" @click.prevent="jumpTo(p.line)">line {{ p.line }}: </a>
                <span v-else-if="p.file && p.file !== path" class="text-grey-7">{{ p.file }}: </span>
                {{ p.message }}{{ p.data?.acknowledged ? ' (acknowledged)' : '' }}
              </template>
            </DiagnosticItem>
          </q-list>
          <q-card-section v-else class="text-grey-7">{{ summary ? 'No problems.' : 'Checking…' }}</q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>
