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
// Every kind of script-vs-SRT divergence is reviewable here, not only mishearings.
const REVIEW_CODES = ['CUE_MISTRANSCRIPTION', 'CUE_PARAPHRASE', 'CUE_CUT', 'CUE_INSERTION'];
const FLAG_TITLE: Record<string, string> = {
  mistranscription: 'Suspected mis-transcription',
  paraphrase: 'Wording differs from the script',
  cut: 'Script text missing from the SRT',
  insertion: 'Extra words in the SRT, not in the script',
};

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
const divergences = shallowRef<Diagnostic[]>([]);   // reviewable CUE_* divergences, English only
let seekedToStart = false;

// Bulk mode: every cue as its own plain text box, for a punctuation-style pass over the whole
// file in one sitting. Flag awareness stays in single-cue mode on purpose - this is just the
// delivered text. bulkBaseline is what's actually on disk (as last loaded); bulkDrafts is what's
// typed. Comparing the two (not re-deriving from cueList on every keystroke) is what lets a
// single Save all batch every changed cue into one bcn caption --cues-json call instead of one
// bcn cues/subtitles rebuild per box, which would make a whole-file pass unusably slow.
const mode = ref<'single' | 'bulk'>('single');
const bulkDrafts = ref<string[]>([]);
const bulkBaseline = ref<string[]>([]);
const bulkDirtyCount = computed(() => bulkDrafts.value.reduce((n, t, i) => n + (t !== bulkBaseline.value[i] ? 1 : 0), 0));

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
    divergences.value = r.outstanding.filter((d) => REVIEW_CODES.includes(d.code) && !!d.data?.id && !d.data?.spans_boundary);
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

function switchMode(target: 'single' | 'bulk') {
  if (target === mode.value) return;
  const pendingDirty = mode.value === 'single' ? dirty.value : bulkDirtyCount.value > 0;
  const go = () => {
    if (target === 'bulk') {
      video.value?.pause();
      bulkBaseline.value = cueList.value.map((r) => r.text);
      bulkDrafts.value = [...bulkBaseline.value];
    } else {
      dirty.value = false;
    }
    mode.value = target;
  };
  if (!pendingDirty) { go(); return; }
  const count = bulkDirtyCount.value;
  void confirm({ title: `Discard the unsaved edit${mode.value === 'bulk' && count > 1 ? 's' : ''}?`, ok: 'Discard', danger: true,
    lines: [mode.value === 'single' ? 'The text typed for the current cue has not been saved.'
      : `${count} cue${count > 1 ? 's have' : ' has'} unsaved text.`] }).then((ok) => { if (ok) go(); });
}
// Once a bulk save's rebuild lands, resync to what's actually delivered (e.g. bcn subtitles' own
// line-wrapping) - safe to do unconditionally because every textarea is disabled while saving, so
// nothing can be mid-edit when this fires.
watch(cueList, () => {
  if (mode.value !== 'bulk' || saving.value) return;
  bulkBaseline.value = cueList.value.map((r) => r.text);
  bulkDrafts.value = [...bulkBaseline.value];
});

// Shared tail of every caption write, single-cue or bulk: bcn cues/subtitles refuse to read
// edit/master.srt within 2s of it changing (FS_UNSTABLE, tooling/bcn/runner.py's
// require_input(..., stable=True)) - it's built for a slow cloud sync still propagating the
// write, but it applies just as much to our own save landing milliseconds earlier, so wait past
// that window before rebuilding. Anchored to when the write actually finished (not blindly from
// here) so time already spent above - a review submission, SSE round trips - counts toward the
// wait instead of stacking on top of it.
async function rebuildAfterEdit(captionDoneAt: number) {
  const STABLE_BUFFER_MS = 2500;
  const remaining = Math.max(0, STABLE_BUFFER_MS - (Date.now() - captionDoneAt));
  if (remaining > 0) await new Promise((r) => setTimeout(r, remaining));
  // subtitles --lang en requires cues to be fresh first; zh's own freshness check already
  // covers the edited file directly, so it needs no cues rebuild (tooling/bcn/commands/subtitles.py).
  if (props.lang === 'en') {
    const cues = await beacon.runJob('cues', [topicPath(props.id)], {});
    await beacon.awaitJob(cues.id);
  }
  const rebuild = await beacon.runJob('subtitles', [topicPath(props.id)], { lang: props.lang });
  await beacon.awaitJob(rebuild.id);
  buildTrackSrc();   // reload: the next cuechange (or the bulk list) reflects what was actually saved
  void loadDivergences();
}

async function save(overrideText?: string) {
  if (activeCueIdx.value === null || saving.value) return;
  const cueIdx = activeCueIdx.value;
  const text = overrideText ?? draftText.value;
  // A divergence's id is a hash of its (script, srt) pair, so once the text changes the old id
  // stops matching anything bcn cues finds — capture it now, before that happens, so the review
  // decision below still has a valid --item to record against.
  const flaggedBefore = activeDivergence.value && !activeDivergence.value.data!.review ? activeDivergence.value : null;
  // Otherwise the playhead keeps advancing through the whole save/rebuild chain, cuechange swaps
  // in whatever cue it reaches next (dirty is already false by then), and the Save button sits
  // disabled while the box appears to change out from under the edit that's still in flight.
  video.value?.pause();
  saving.value = true;
  try {
    const job = await beacon.runJob('caption', [topicPath(props.id)],
      { lang: props.lang, cue: cueIdx + 1, caption_text: text });
    const result = await beacon.awaitJob(job.id);
    if (result.state !== 'done') { beacon.toast('That edit did not save; see Jobs.', true); return; }
    const captionDoneAt = Date.now();   // anchors the FS_UNSTABLE wait below to the real write time
    draftText.value = text;
    dirty.value = false;
    // Record the decision now, while the pre-edit id is still in cues-report.json (bcn review
    // looks it up there), before cues reruns and regenerates that report — a saved cue's text is
    // itself the review decision, so this always leaves a record, whether or not the new wording
    // still diverges from the script afterwards. Submission (not completion) is awaited: the
    // backend's per-topic overlap lock already serialises this ahead of the cues job below (same
    // target, lower job id => dequeued first), so there is nothing left for this await to protect.
    if (flaggedBefore) {
      try {
        // applied: true - the text above is already live in edit/master.srt (bcn caption just
        // wrote it directly), so bcn subtitles must never try to regex-substitute this decision
        // in again; the old "heard" span is often a short, common fragment that could otherwise
        // resurface by coincidence in later, unrelated text and corrupt it.
        await beacon.runJob('review', [topicPath(props.id)],
          { item: flaggedBefore.data!.id!, correct: text.replace(/\s+/g, ' ').trim().slice(0, 200), applied: true });
      } catch { /* beacon.runJob already toasted; the text edit itself still saved */ }
    }
    await rebuildAfterEdit(captionDoneAt);
  } catch { /* beacon.runJob already toasted the submit failure */ }
  finally { saving.value = false; }
}

async function saveBulk() {
  if (saving.value) return;
  const edits: Record<string, string> = {};
  bulkDrafts.value.forEach((text, i) => { if (text !== bulkBaseline.value[i]) edits[String(i + 1)] = text; });
  if (!Object.keys(edits).length) return;
  const blank = Object.entries(edits).find(([, text]) => !text.trim());
  if (blank) { beacon.toast(`Cue ${blank[0]} can't be saved empty.`, true); return; }
  video.value?.pause();
  saving.value = true;
  try {
    const job = await beacon.runJob('caption', [topicPath(props.id)],
      { lang: props.lang, cues_json: JSON.stringify(edits) });
    const result = await beacon.awaitJob(job.id);
    if (result.state !== 'done') { beacon.toast('That edit did not save; see Jobs.', true); return; }
    const captionDoneAt = Date.now();
    // What was just written is now the known-saved baseline; the cueList watcher reconciles it
    // against the delivered text once the rebuild below actually lands.
    bulkBaseline.value = [...bulkDrafts.value];
    await rebuildAfterEdit(captionDoneAt);
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
  // Whole words only, and only if the wording occurs exactly once: a bare "the" or "a" would
  // otherwise match inside "other" or hit the wrong occurrence, and quietly rewrite the wrong text.
  const pattern = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+');
  const re = new RegExp(`(?<![\\p{L}\\p{N}])${pattern}(?![\\p{L}\\p{N}])`, 'gu');
  const hits = current.match(re);
  if (!hits || hits.length !== 1) return null;
  return current.replace(re, () => to);
}

// "Use script" is only offered where there is both a script span and an SRT span to swap, and it
// always shows the line as it will read afterwards before anything is written. The script span
// can be a longer, differently-worded passage than what was captioned (a reworded sentence, a
// mishearing that spans several words), so a one-click swap that is wrong is worse than none.
const canUseScript = computed(() => {
  const d = activeDivergence.value?.data;
  return !!d && !!(d.script ?? '').trim() && !!(d.srt ?? '').trim();
});

// A flagged cue has exactly two outcomes: deliver the approved script instead (an edit, same as
// any other — bcn caption, then the usual cues/subtitles rebuild), or keep the subtitle as it
// stands in the box — edited or not — which the Save button above now treats as the same
// decision (see the end of save()).
async function useScript(d: Diagnostic) {
  const scriptText = (d.data!.script ?? '').trim();
  const srtText = (d.data!.srt ?? '').trim();
  if (!scriptText || !srtText) return;
  const next = spanReplace(draftText.value, srtText, scriptText);
  if (next === null) {
    beacon.toast('Could not find that exact wording once in this cue (it may run into the next cue) — edit it by hand instead.', true);
    return;
  }
  const ok = await confirm({ title: 'Replace the captioned wording with the script?', ok: 'Replace', danger: true,
    lines: [`SRT reads: “${srtText}”`, `Script reads: “${scriptText}”`, `The cue will read: “${next.replace(/\s+/g, ' ')}”`] });
  if (ok) void save(next);
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
function onBulkTextareaKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void saveBulk(); }
}
function togglePlay() {
  if (!video.value) return;
  if (video.value.paused) void video.value.play(); else video.value.pause();
}
function onKey(e: KeyboardEvent) {
  if (mode.value === 'bulk') return;   // no single active cue/video to step through or play here
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
      <div class="row items-center gap-xs q-mr-md">
        <q-btn :flat="mode !== 'single'" :unelevated="mode === 'single'" dense no-caps size="sm" color="primary"
          label="Edit" @click="switchMode('single')" />
        <q-btn :flat="mode !== 'bulk'" :unelevated="mode === 'bulk'" dense no-caps size="sm" color="primary"
          label="Edit all cues" @click="switchMode('bulk')">
          <q-tooltip>Every cue as its own box, for a punctuation pass over the whole file</q-tooltip>
        </q-btn>
      </div>
      <span v-if="mode === 'single'" class="text-caption text-grey-5">[ / ] step cues · Space play/pause · ⌘/Ctrl+Enter save</span>
      <span v-else class="text-caption text-grey-5">⌘/Ctrl+Enter save all</span>
    </div>

    <div v-if="error" class="ce-error">{{ error }}</div>
    <div v-else class="ce-body" :class="{ 'ce-body-bulk': mode === 'bulk' }">
      <div class="ce-player">
        <video ref="video" v-show="mode === 'single'" :src="videoSrc" preload="metadata" playsinline controls @loadedmetadata="onLoadedMeta">
          <track v-if="trackSrc" ref="trackEl" kind="subtitles" :srclang="lang" :src="trackSrc" @load="onTrackLoad">
        </video>
        <div v-if="mode === 'single'" class="ce-edit">
          <div v-if="activeDivergence && !activeDivergence.data!.review" class="ce-flag">
            <div class="text-caption text-warning">{{ FLAG_TITLE[String(activeDivergence.data!.kind)] ?? 'Differs from the script' }}</div>
            <div v-if="activeDivergence.data!.script" class="text-caption">Script: “{{ activeDivergence.data!.script }}”</div>
            <div v-if="activeDivergence.data!.srt" class="text-caption">SRT: “{{ activeDivergence.data!.srt }}”</div>
            <div class="row items-center gap-sm q-mt-xs">
              <q-btn v-if="canUseScript" outline dense no-caps size="sm" color="warning" label="Use script" @click="useScript(activeDivergence)">
                <q-tooltip>Swap just the flagged wording for the script's (you confirm the result first)</q-tooltip>
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
      <div v-if="mode === 'single'" class="ce-list">
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
      <div v-else class="ce-bulk">
        <div class="ce-bulk-bar row items-center gap-sm">
          <span class="text-caption text-grey-5">{{ cueList.length }} cues</span>
          <q-space />
          <span v-if="bulkDirtyCount" class="text-caption text-warning">{{ bulkDirtyCount }} unsaved</span>
          <q-btn unelevated dense no-caps color="primary" label="Save all" :loading="saving"
            :disable="!bulkDirtyCount" @click="saveBulk">
            <q-tooltip>⌘/Ctrl+Enter</q-tooltip>
          </q-btn>
        </div>
        <div class="ce-bulk-list">
          <div v-for="(row, i) in cueList" :key="row.index" class="ce-bulk-row"
            :class="{ dirty: bulkDrafts[i] !== bulkBaseline[i] }">
            <span class="text-caption text-grey-5 ce-bulk-time">{{ fmtTime(row.start) }}</span>
            <textarea class="ce-bulk-box" :value="bulkDrafts[i]" :disabled="saving" spellcheck="false"
              @input="(e) => { bulkDrafts[i] = (e.target as HTMLTextAreaElement).value; }" @keydown="onBulkTextareaKey" />
          </div>
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

/* Bulk mode: the video stays mounted (v-show, not v-if) so its <track> keeps supplying cueList,
   but collapses to nothing visible - the whole row is the cue list instead. */
.ce-body-bulk .ce-player { flex: 0; padding: 0; overflow: hidden; }
.ce-bulk { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.ce-bulk-bar { padding: 10px 16px; border-bottom: 1px solid #2a2a2a; background: #1a1a1a; }
.ce-bulk-list { flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 6px; }
.ce-bulk-row { display: flex; align-items: flex-start; gap: 10px; }
.ce-bulk-time { width: 56px; flex: none; padding-top: 10px; text-align: right; }
.ce-bulk-row.dirty .ce-bulk-time { color: var(--q-warning); }
.ce-bulk-box {
  flex: 1; min-width: 0; font: inherit; font-size: 15px; line-height: 1.35; color: #1a1a1a;
  background: #fff; border: 1px solid #3a3a3a; border-radius: 4px; padding: 8px 10px; resize: vertical;
  height: 60px; box-sizing: border-box;
}
.ce-bulk-row.dirty .ce-bulk-box { border-color: var(--q-warning); }
.ce-bulk-box:focus { outline: 2px solid var(--q-primary); outline-offset: -1px; }
.ce-bulk-box:disabled { opacity: 0.6; }
</style>
