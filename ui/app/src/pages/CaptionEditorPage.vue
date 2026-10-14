<script setup lang="ts">
// Caption quick-edit: play the video, see the subtitle showing right now in an editable box,
// pause, retype it, save — a fast "final pass" tool, and the one place subtitle text gets
// fixed, full stop. A cue flagged by bcn cues as a suspected mis-transcription offers exactly
// two decisions: "Use script" reverts to the approved wording, or edit the box (or leave it
// untouched) and Save to keep the subtitle — Save itself clears the flag either way, since
// saving a flagged cue's text *is* the review decision. This folds in what used to be a
// separate Mis-transcriptions review queue, since both were really "decide what this cue's
// delivered text should be."
// Cues are read straight off the native <track>'s TextTrackCueList, never custom-parsed:
// its order is 0-based and lines up with the SRT's 1-based cue position, since bcn always
// renumbers 1..N on write.
// Keys: [ / ] step between cues, Space play/pause (same convention as the Video pane).
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { useRouter } from 'vue-router';
import type { Diagnostic, DiagnosticsResponse, TopicResponse } from '@beacon/shared';
import { api, fileUrl } from '@/api';
import StateChip from '@/components/StateChip.vue';
import { confirm } from '@/composables/confirm';
import { fmtTime, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

interface CueRow { index: number; start: number; end: number; text: string }

const DEFAULT_RATE = 1.2;

const props = defineProps<{ id: string; lang: 'en' | 'zh'; startAt?: number | null }>();
const router = useRouter();
const beacon = useBeacon();

const data = shallowRef<TopicResponse | null>(null);
const error = ref<string | null>(null);
const video = ref<HTMLVideoElement | null>(null);
const trackEl = ref<HTMLTrackElement | null>(null);
const trackSrc = ref('');
const cueList = ref<CueRow[]>([]);
const activeCueIdx = ref<number | null>(null);   // 0-based; null = no cue at this time
const draftText = ref('');
const dirty = ref(false);
const saving = ref(false);
const divergences = shallowRef<Diagnostic[]>([]);   // CUE_MISTRANSCRIPTION items, English only
let seekedToStart = false;

const media = computed(() => data.value?.show.results?.[0]?.media ?? null);
const videoSrc = computed(() => (media.value?.master ? fileUrl(media.value.master) : ''));
const subtitlePath = computed(() => media.value?.subtitles[props.lang] ?? null);
// Keyed by 0-based cue index (a divergence's `cue` field is the same 1-based SRT position
// bcn caption takes, so index = cue - 1 — matching how cueList itself is indexed). More than
// one divergence can land on the same cue: a repeatedly-edited cue accumulates one synthesised,
// already-reviewed entry per past decision that no longer recurs live (bcn cues keeps only the
// most recent such entry, but that can still be a different one from whatever's currently live),
// alongside the current live divergence if the wording still doesn't match the script. Pick
// deterministically rather than depending on whatever order the server happens to list them in:
// an open (unreviewed) flag always wins, and between two reviewed entries, the more recent one.
const divergenceByCue = computed(() => {
  const m = new Map<number, Diagnostic>();
  for (const d of divergences.value) {
    if (d.data?.cue == null) continue;
    const idx = d.data.cue - 1;
    const existing = m.get(idx);
    if (!existing) { m.set(idx, d); continue; }
    const existingOpen = !existing.data?.review;
    const dOpen = !d.data?.review;
    if (existingOpen && !dOpen) continue;   // keep the open flag over a reviewed one
    if (!existingOpen && dOpen) { m.set(idx, d); continue; }   // a fresh open flag displaces it
    if (!existingOpen && !dOpen && (d.data!.review!.at ?? '') > (existing.data!.review!.at ?? '')) m.set(idx, d);
  }
  return m;
});
const activeDivergence = computed(() => (activeCueIdx.value !== null ? divergenceByCue.value.get(activeCueIdx.value) ?? null : null));
const pendingFlag = computed(() => !!(activeDivergence.value && !activeDivergence.value.data!.review));

function buildTrackSrc() {
  // Cache-bust with the current time, not the file's mtime: we want the browser to refetch
  // the instant we know it changed, not wait for a stamp lookup that may itself be stale.
  trackSrc.value = subtitlePath.value ? fileUrl(subtitlePath.value, String(Date.now()), 'format=vtt') : '';
}

async function loadDivergences() {
  if (props.lang !== 'en') return;   // mis-transcription review has always been English-only
  try {
    const r = await api<DiagnosticsResponse>(`/api/diagnostics?path=${topicPath(props.id)}`);
    divergences.value = r.outstanding.filter((d) => d.code === 'CUE_MISTRANSCRIPTION');
  } catch { /* non-critical: the editor still works without the flags */ }
}

async function load() {
  try {
    data.value = await api<TopicResponse>(`/api/topic/${props.id}`);
    error.value = subtitlePath.value ? null : `This topic has no ${props.lang === 'zh' ? 'Mandarin' : 'English'} subtitles yet.`;
    buildTrackSrc();
    void loadDivergences();
  } catch (e) {
    error.value = (e as Error).message;
  }
}
void load();

function refreshCueList() {
  const cues = trackEl.value?.track?.cues;
  if (!cues) return;
  const rows: CueRow[] = [];
  for (let i = 0; i < cues.length; i++) {
    const c = cues[i] as VTTCue;
    rows.push({ index: i, start: c.startTime, end: c.endTime, text: c.text });
  }
  cueList.value = rows;
}

function syncFromActive() {
  const track = trackEl.value?.track;
  const active = track?.activeCues?.[0] as VTTCue | undefined;
  if (!active) { activeCueIdx.value = null; return; }
  const cues = track!.cues!;
  let idx: number | null = null;
  for (let i = 0; i < cues.length; i++) if (cues[i] === active) idx = i;
  if (idx === null || idx === activeCueIdx.value || dirty.value) return;   // never clobber an in-progress edit
  activeCueIdx.value = idx;
  draftText.value = active.text;
}

// A dynamically-inserted <track> never fetches on its own — mode starts 'disabled' and the
// browser only begins loading once something sets it to 'hidden'/'showing'. Do that the moment
// the element exists, not inside its own @load handler (which would never fire without this).
watch(trackEl, (el) => {
  const track = el?.track;
  if (!track) return;
  track.mode = 'hidden';   // populates .cues without the browser burning in captions over the video
  track.addEventListener('cuechange', syncFromActive);
});
function onTrackLoad() {
  refreshCueList();
  // Deep-linked from the Mis-transcriptions list (or anywhere else) at a specific time: jump
  // straight to the cue nearest that time, once, rather than whatever's active at time 0.
  if (!seekedToStart && props.startAt != null && cueList.value.length) {
    seekedToStart = true;
    let nearest = 0;
    for (let i = 0; i < cueList.value.length; i++) if (cueList.value[i].start <= props.startAt) nearest = i;
    selectCue(nearest);
    return;
  }
  syncFromActive();
}

function selectCue(i: number) {
  const row = cueList.value[i];
  if (!row) return;
  const go = () => {
    activeCueIdx.value = i;
    draftText.value = row.text;
    dirty.value = false;
    if (video.value) video.value.currentTime = row.start + 0.01;
  };
  if (dirty.value) {
    void confirm({ title: 'Discard the unsaved edit?', ok: 'Discard', danger: true,
      lines: ['The text typed for the current cue has not been saved.'] }).then((ok) => { if (ok) go(); });
  } else go();
}
function nextCue() { const i = (activeCueIdx.value ?? -1) + 1; if (i < cueList.value.length) selectCue(i); }
function prevCue() { const i = (activeCueIdx.value ?? 0) - 1; if (i >= 0) selectCue(i); }

async function save(overrideText?: string) {
  if (activeCueIdx.value === null || saving.value) return;
  const cueIdx = activeCueIdx.value;
  const text = overrideText ?? draftText.value;
  // A divergence's id is a hash of its (script, srt) pair, so once the text changes the old id
  // stops matching anything bcn cues finds — capture it now, before that happens, so the review
  // decision below still has a valid --item to record against.
  const flaggedBefore = activeDivergence.value && !activeDivergence.value.data!.review ? activeDivergence.value : null;
  saving.value = true;
  try {
    const job = await beacon.runJob('caption', [topicPath(props.id)],
      { lang: props.lang, cue: cueIdx + 1, caption_text: text });
    const result = await beacon.awaitJob(job.id);
    if (result.state !== 'done') { beacon.toast('That edit did not save; see Jobs.', true); return; }
    draftText.value = text;
    dirty.value = false;
    // Record the decision now, while the pre-edit id is still in cues-report.json (bcn review
    // looks it up there). Wait a moment for it to persist to review.json before cues reruns and
    // regenerates that report — a saved cue's text is itself the review decision, so this always
    // leaves a record, whether or not the new wording still diverges from the script afterwards.
    if (flaggedBefore) {
      try {
        // applied: true - the text above is already live in edit/master.srt (bcn caption just
        // wrote it directly), so bcn subtitles must never try to regex-substitute this decision
        // in again; the old "heard" span is often a short, common fragment that could otherwise
        // resurface by coincidence in later, unrelated text and corrupt it.
        const rj = await beacon.runJob('review', [topicPath(props.id)],
          { item: flaggedBefore.data!.id!, correct: text.replace(/\s+/g, ' ').trim().slice(0, 200), applied: true });
        await beacon.awaitJob(rj.id);
      } catch { /* beacon.runJob already toasted; the text edit itself still saved */ }
    }
    // Both cues and subtitles refuse to read edit/master.srt within 2s of it changing
    // (FS_UNSTABLE, tooling/bcn/runner.py's require_input(..., stable=True)) - it's built for
    // a slow cloud sync still propagating the write, but it applies just as much to our own
    // save landing milliseconds earlier, so wait past that window before rebuilding.
    await new Promise((r) => setTimeout(r, 2500));
    // subtitles --lang en requires cues to be fresh first; zh's own freshness check already
    // covers the edited file directly, so it needs no cues rebuild (tooling/bcn/commands/subtitles.py).
    if (props.lang === 'en') {
      const cues = await beacon.runJob('cues', [topicPath(props.id)], {});
      await beacon.awaitJob(cues.id);
    }
    const rebuild = await beacon.runJob('subtitles', [topicPath(props.id)], { lang: props.lang });
    await beacon.awaitJob(rebuild.id);
    buildTrackSrc();   // reload: the next cuechange reflects what was actually saved
    void loadDivergences();
  } catch { /* beacon.runJob already toasted the submit failure */ }
  finally { saving.value = false; }
}

// d.data.script/d.data.srt are only the differing span (a word or short phrase), not the full
// cue line — align.py's diff runs on tokenized text, not whole lines. The old review-based
// mechanism (apply_corrections, tooling/bcn/commands/subtitles.py) reconstructed the full line
// by regex-replacing just that span inside the cue's current text; do the same thing client-side
// before handing bcn caption a complete replacement line, or "Use script" would wipe the rest
// of the sentence rather than fixing the one word.
function spanReplace(current: string, from: string, to: string): string | null {
  const words = from.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  const pattern = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+');
  const re = new RegExp(pattern);
  return re.test(current) ? current.replace(re, to) : null;
}

// A flagged cue has exactly two outcomes: deliver the approved script instead (an edit, same as
// any other — bcn caption, then the usual cues/subtitles rebuild), or keep the subtitle as it
// stands in the box — edited or not — which the Save button above now treats as the same
// decision (see the end of save()).
function useScript(d: Diagnostic) {
  const scriptText = (d.data!.script ?? '').trim();
  const srtText = (d.data!.srt ?? '').trim();
  if (!scriptText) return;
  const next = srtText ? spanReplace(draftText.value, srtText, scriptText) : null;
  if (next === null) {
    beacon.toast('Could not find the exact wording to replace in this cue — edit it by hand instead.', true);
    return;
  }
  void save(next);
}
async function commitCue() {
  if (activeCueIdx.value === null || saving.value) return;
  // Nothing typed and nothing to patch: skip the caption/cues/subtitles rebuild chain entirely
  // and just record the decision directly — the delivered text truly isn't changing.
  if (!dirty.value && pendingFlag.value) { await acceptAsIs(activeDivergence.value!); return; }
  await save();
}
async function acceptAsIs(d: Diagnostic) {
  try {
    const job = await beacon.runJob('review', [topicPath(props.id)], { item: d.data!.id!, accept: true });
    const result = await beacon.awaitJob(job.id);
    if (result.state === 'done') await loadDivergences();
    else beacon.toast('That decision did not save; see Jobs.', true);
  } catch { /* beacon.runJob already toasted */ }
}
async function undoReview(d: Diagnostic) {
  try {
    const job = await beacon.runJob('review', [topicPath(props.id)], { item: d.data!.id!, clear: true });
    const result = await beacon.awaitJob(job.id);
    if (result.state === 'done') await loadDivergences();
    else beacon.toast('That decision did not save; see Jobs.', true);
  } catch { /* beacon.runJob already toasted */ }
}

function onLoadedMeta() { if (video.value) video.value.playbackRate = DEFAULT_RATE; }
function onTextareaKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void commitCue(); }
}
function togglePlay() {
  if (!video.value) return;
  if (video.value.paused) void video.value.play(); else video.value.pause();
}
function onKey(e: KeyboardEvent) {
  const tag = (document.activeElement as HTMLElement | null)?.tagName;
  if (tag && ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
  if (document.querySelector('.q-dialog')) return;
  if (e.key === ']') { nextCue(); e.preventDefault(); }
  else if (e.key === '[') { prevCue(); e.preventDefault(); }
  else if (e.key === ' ') { togglePlay(); e.preventDefault(); }
}

onMounted(() => {
  document.body.classList.add('captioning');
  document.addEventListener('keydown', onKey);
});
onBeforeUnmount(() => {
  document.body.classList.remove('captioning');
  document.removeEventListener('keydown', onKey);
  trackEl.value?.track?.removeEventListener('cuechange', syncFromActive);
  const v = video.value;
  if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
});

function leave() { void router.push(`/topic/${props.id}`); }
</script>

<template>
  <div class="caption-editor">
    <div class="ce-header">
      <q-btn flat dense round icon="close" @click="leave"><q-tooltip>Back to topic</q-tooltip></q-btn>
      <div class="text-subtitle1">{{ id }} · Captions ({{ lang === 'zh' ? 'Mandarin' : 'English' }})</div>
      <q-space />
      <span class="text-caption text-grey-5">[ / ] step cues · Space play/pause · ⌘/Ctrl+Enter save</span>
    </div>

    <div v-if="error" class="ce-error">{{ error }}</div>
    <div v-else class="ce-body">
      <div class="ce-player">
        <video ref="video" :src="videoSrc" preload="metadata" playsinline controls @loadedmetadata="onLoadedMeta">
          <track v-if="trackSrc" ref="trackEl" kind="subtitles" :srclang="lang" :src="trackSrc" @load="onTrackLoad">
        </video>
        <div class="ce-edit">
          <div v-if="activeDivergence && !activeDivergence.data!.review" class="ce-flag">
            <div class="text-caption text-warning">Suspected mis-transcription — script reads: "{{ activeDivergence.data!.script }}"</div>
            <div class="row items-center gap-sm q-mt-xs">
              <q-btn outline dense no-caps size="sm" color="warning" label="Use script" @click="useScript(activeDivergence)">
                <q-tooltip>Replace the delivered text with the approved script</q-tooltip>
              </q-btn>
              <span class="text-caption text-grey-5">or edit below and Save to keep the subtitle</span>
            </div>
          </div>
          <div v-else-if="activeDivergence" class="ce-flag ce-flag-done row items-center gap-sm">
            <StateChip kind="ok" :label="activeDivergence.data!.review!.decision === 'accept' ? 'kept SRT' : `corrected to “${activeDivergence.data!.review!.text}”`" />
            <q-btn flat dense no-caps size="sm" label="Undo" @click="undoReview(activeDivergence)" />
          </div>
          <q-input v-if="activeCueIdx !== null" :model-value="draftText" type="textarea" dense outlined :dark="false"
            bg-color="white" color="dark" class="ce-textarea text-dark" aria-label="Cue text" input-style="height: 96px"
            @update:model-value="(v) => { draftText = String(v ?? ''); dirty = true; }" @keydown="onTextareaKey" />
          <div v-else class="ce-gap text-grey-5">No cue at this time — seek to a line to edit it.</div>
          <div class="ce-edit-row row items-center gap-sm q-mt-sm">
            <q-btn outline dense no-caps label="⟨ cue" @click="prevCue" />
            <q-btn outline dense no-caps label="cue ⟩" @click="nextCue" />
            <q-space />
            <span v-if="dirty" class="text-caption text-warning">Unsaved</span>
            <q-btn unelevated dense no-caps color="primary" :label="pendingFlag ? 'Keep subtitles' : 'Save'" :loading="saving"
              :disable="activeCueIdx === null || (!dirty && !pendingFlag)" @click="commitCue">
              <q-tooltip>{{ pendingFlag && !dirty ? 'Mark this reviewed without changing the delivered text' : '⌘/Ctrl+Enter' }}</q-tooltip>
            </q-btn>
          </div>
        </div>
      </div>
      <div class="ce-list">
        <div v-for="row in cueList" :key="row.index" class="ce-row" :class="{ active: row.index === activeCueIdx }"
          @click="selectCue(row.index)">
          <span class="text-caption text-grey-5">{{ fmtTime(row.start) }}</span>
          <q-icon v-if="divergenceByCue.get(row.index) && !divergenceByCue.get(row.index)!.data!.review" name="warning" color="warning" size="14px">
            <q-tooltip>Suspected mis-transcription — not yet reviewed</q-tooltip>
          </q-icon>
          <q-icon v-else-if="divergenceByCue.get(row.index)" name="check_circle" color="positive" size="14px">
            <q-tooltip>Reviewed</q-tooltip>
          </q-icon>
          <span class="ce-row-text">{{ row.text || '—' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.caption-editor { position: fixed; inset: 0; display: flex; flex-direction: column; background: #111; color: #eee; }
.ce-header { display: flex; align-items: center; gap: 8px; padding: 8px 16px; background: #1a1a1a; border-bottom: 1px solid #2a2a2a; }
.ce-error { padding: 24px; color: #ccc; }
.ce-body { flex: 1; display: flex; min-height: 0; }
.ce-player { flex: 2; display: flex; flex-direction: column; padding: 16px; min-width: 0; }
.ce-player video { width: 100%; max-height: 55vh; background: #000; border-radius: 4px; }
.ce-edit { margin-top: 12px; display: flex; flex-direction: column; align-items: center; }
.ce-flag { width: 40vw; margin-bottom: 8px; padding: 8px; border: 1px solid var(--q-warning); border-radius: 4px; box-sizing: border-box; }
.ce-flag-done { border-color: #333; padding: 6px 8px; }
/* A fixed height (not autogrow) keeps the Save/Use-script row at the same spot regardless of
   how many lines a cue wraps to - longer text scrolls inside the box instead of pushing the
   buttons down. .ce-gap matches it so the row doesn't shift when there's no active cue either. */
.ce-textarea { font-size: 16px; width: 40vw; }
.ce-edit-row { width: 40vw; }
.ce-gap { width: 40vw; height: 96px; padding: 16px; text-align: center; border: 1px dashed #333; border-radius: 4px;
  box-sizing: border-box; display: flex; align-items: center; justify-content: center; }
.ce-list { flex: 1; max-width: 360px; overflow-y: auto; border-left: 1px solid #2a2a2a; padding: 8px; }
.ce-row { display: flex; gap: 8px; padding: 6px 8px; border-radius: 4px; cursor: pointer; }
.ce-row:hover { background: rgba(255, 255, 255, 0.06); }
.ce-row.active { background: rgba(255, 255, 255, 0.12); }
.ce-row-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
