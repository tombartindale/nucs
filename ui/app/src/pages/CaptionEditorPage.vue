<script setup lang="ts">
// Caption quick-edit: play the video, see the subtitle showing right now in an editable box,
// pause, retype it, save — a fast "final pass" tool, distinct from the Mis-transcriptions
// review queue (one ASR-vs-script divergence at a time) and the full script editor.
// Cues are read straight off the native <track>'s TextTrackCueList, never custom-parsed:
// its order is 0-based and lines up with the SRT's 1-based cue position, since bcn always
// renumbers 1..N on write.
// Keys: [ / ] step between cues, Space play/pause (same convention as the Video pane).
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { useRouter } from 'vue-router';
import type { TopicResponse } from '@beacon/shared';
import { api, fileUrl } from '@/api';
import { confirm } from '@/composables/confirm';
import { fmtTime, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

interface CueRow { index: number; start: number; end: number; text: string }

const DEFAULT_RATE = 1.2;

const props = defineProps<{ id: string; lang: 'en' | 'zh' }>();
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

const media = computed(() => data.value?.show.results?.[0]?.media ?? null);
const videoSrc = computed(() => (media.value?.master ? fileUrl(media.value.master) : ''));
const subtitlePath = computed(() => media.value?.subtitles[props.lang] ?? null);

function buildTrackSrc() {
  // Cache-bust with the current time, not the file's mtime: we want the browser to refetch
  // the instant we know it changed, not wait for a stamp lookup that may itself be stale.
  trackSrc.value = subtitlePath.value ? fileUrl(subtitlePath.value, String(Date.now()), 'format=vtt') : '';
}

async function load() {
  try {
    data.value = await api<TopicResponse>(`/api/topic/${props.id}`);
    error.value = subtitlePath.value ? null : `This topic has no ${props.lang === 'zh' ? 'Mandarin' : 'English'} subtitles yet.`;
    buildTrackSrc();
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

async function save() {
  if (activeCueIdx.value === null || saving.value) return;
  saving.value = true;
  try {
    const job = await beacon.runJob('caption', [topicPath(props.id)],
      { lang: props.lang, cue: activeCueIdx.value + 1, caption_text: draftText.value });
    const result = await beacon.awaitJob(job.id);
    if (result.state !== 'done') { beacon.toast('That edit did not save; see Jobs.', true); return; }
    dirty.value = false;
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
  } catch { /* beacon.runJob already toasted the submit failure */ }
  finally { saving.value = false; }
}

function onLoadedMeta() { if (video.value) video.value.playbackRate = DEFAULT_RATE; }
function onTextareaKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void save(); }
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
          <q-input v-if="activeCueIdx !== null" :model-value="draftText" type="textarea" autogrow dense outlined :dark="false"
            bg-color="white" color="dark" class="ce-textarea text-dark" aria-label="Cue text"
            @update:model-value="(v) => { draftText = String(v ?? ''); dirty = true; }" @keydown="onTextareaKey" />
          <div v-else class="ce-gap text-grey-5">No cue at this time — seek to a line to edit it.</div>
          <div class="ce-edit-row row items-center gap-sm q-mt-sm">
            <q-btn outline dense no-caps label="⟨ cue" @click="prevCue" />
            <q-btn outline dense no-caps label="cue ⟩" @click="nextCue" />
            <q-space />
            <span v-if="dirty" class="text-caption text-warning">Unsaved</span>
            <q-btn unelevated dense no-caps color="primary" label="Save" :loading="saving"
              :disable="activeCueIdx === null || !dirty" @click="save">
              <q-tooltip>⌘/Ctrl+Enter</q-tooltip>
            </q-btn>
          </div>
        </div>
      </div>
      <div class="ce-list">
        <div v-for="row in cueList" :key="row.index" class="ce-row" :class="{ active: row.index === activeCueIdx }"
          @click="selectCue(row.index)">
          <span class="text-caption text-grey-5">{{ fmtTime(row.start) }}</span>
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
.ce-textarea { font-size: 16px; width: 40vw; }
.ce-edit-row { width: 40vw; }
.ce-gap { width: 40vw; padding: 16px; text-align: center; border: 1px dashed #333; border-radius: 4px; box-sizing: border-box; }
.ce-list { flex: 1; max-width: 360px; overflow-y: auto; border-left: 1px solid #2a2a2a; padding: 8px; }
.ce-row { display: flex; gap: 8px; padding: 6px 8px; border-radius: 4px; cursor: pointer; }
.ce-row:hover { background: rgba(255, 255, 255, 0.06); }
.ce-row.active { background: rgba(255, 255, 255, 0.12); }
.ce-row-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
