<script setup lang="ts">
// Teleprompter: the topic's narration, full screen, scrolling past a reading line.
// Reads the same parsed script as the Script pane (bcn show), so it is always current.
//
// Keys: Space play/pause · ↑/↓ speed · +/− text size · ←/→ or PageUp/PageDown previous/next
// slide (what most presentation clickers send) · Home back to the top · M mirror · F full screen
// · V voice follow · Esc leave. The control bar stays put.
//
// With voice follow on, Play listens instead of scrolling at a set speed: on-device speech
// recognition finds the word just said and keeps its line on the reading line.
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue';
import { useRouter } from 'vue-router';
import type { ShowScript, TopicResponse } from '@beacon/shared';
import { api } from '@/api';
import { listen, locate, normalise, type Listener } from '@/speechFollow';

const PREFS_KEY = 'beacon-prompter';
const DEFAULTS = { speed: 5, voiceSpeed: 3, size: 56, width: 70, mirror: false, voice: false };

const props = defineProps<{ id: string }>();
const router = useRouter();

function loadPrefs() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch { return { ...DEFAULTS }; }
}
const prefs = reactive<typeof DEFAULTS>(loadPrefs());
function savePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* private window: fine */ }
}

// Narration may carry **bold** and *emphasis*; everything else is shown as written.
function inline(text: string) {
  const esc = text.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]!));
  return esc.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*(?!\s)(.+?)\*(?![*\w])/g, '$1<em>$2</em>');
}
const clock = (ms: number) => { const s = Math.floor(ms / 1000); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };

const script = shallowRef<ShowScript | null>(null);
const error = ref<string | null>(null);
const stage = ref<HTMLDivElement | null>(null);
const textEl = ref<HTMLDivElement | null>(null);
const playing = ref(false);
const statusLine = ref('');
let pos = 0;              // scroll position in px, fractional
let last = 0;
let elapsed = 0;          // ms spent playing
let raf = 0;

// Pixels per second scale with the text, so a speed setting feels the same at any size.
// Speed 5 at the default size and width is roughly 145 words a minute. Voice follow has its own,
// slower, base speed, which the speaker's position then pushes up or down.
const speedKey = () => (prefs.voice ? 'voiceSpeed' : 'speed');
const pxPerSec = () => prefs[speedKey()] * prefs.size * 0.085;
const dividers = () => [...(textEl.value?.querySelectorAll<HTMLElement>('.prompter-divider') || [])];
const eyeY = () => (stage.value?.clientHeight || 0) * 0.33;

function currentSlide() {
  let cur = 0;
  dividers().forEach((d, i) => { if (d.offsetTop - (stage.value?.scrollTop || 0) <= eyeY() + 4) cur = i; });
  return cur;
}
function goToSlide(i: number) {
  const ds = dividers();
  if (!ds.length || !stage.value) return;
  const k = Math.max(0, Math.min(ds.length - 1, i));
  pos = Math.max(0, ds[k].offsetTop - eyeY());
  stage.value.scrollTop = pos;
  moved();
}

// --- voice follow ---
// Every narration word is wrapped in a span once the script is on screen; words[] holds the
// spoken form of each (a written token can be several) and spans[] the span it sits in.
let words: string[] = [];
let spans: HTMLElement[] = [];
let cursor = -1;                    // index into words of the last word said
let target: number | null = null;   // scroll position that puts the last word said on the reading line
let velocity = 0;                   // px/s, eased so speed changes are never a jump
let listener: Listener | null = null;
const voiceNote = ref('');

function wrapWords() {
  words = []; spans = [];
  for (const p of textEl.value?.querySelectorAll('p') || []) {
    const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
    const texts: Text[] = [];
    while (walker.nextNode()) texts.push(walker.currentNode as Text);
    for (const t of texts) {
      const frag = document.createDocumentFragment();
      for (const part of t.data.split(/(\s+)/)) {
        const said = normalise(part);
        if (!said.length) { frag.append(part); continue; }
        const span = document.createElement('span');
        span.className = 'w';
        span.textContent = part;
        frag.append(span);
        for (const w of said) { words.push(w); spans.push(span); }
      }
      t.replaceWith(frag);
    }
  }
}

// The vertical centre of a word, in the stage's scroll coordinates.
function wordY(i: number) {
  const st = stage.value!;
  const r = spans[i].getBoundingClientRect();
  return r.top + r.height / 2 - st.getBoundingClientRect().top + st.scrollTop;
}

// Dim the words already said, touching only the spans between the old and new cursor.
function setCursor(i: number) {
  const [a, b] = i > cursor ? [cursor + 1, i] : [i + 1, cursor];
  for (let k = Math.max(0, a); k <= b && k < spans.length; k++) spans[k].classList.toggle('said', k <= i);
  cursor = i;
}

// After a jump or a manual scroll, the speaker carries on from whatever is on the reading line.
function moved() {
  target = null;
  if (!prefs.voice || !spans.length || !stage.value) return;
  const y = stage.value.scrollTop + eyeY();
  let lo = 0, hi = spans.length;    // first word whose centre is below the reading line
  while (lo < hi) { const mid = (lo + hi) >> 1; if (wordY(mid) <= y) lo = mid + 1; else hi = mid; }
  setCursor(lo - 1);
  listener?.forget();
}

function heard(w: string[]) {
  const i = locate(w, words, cursor + 1);
  if (i === null || i === cursor) return;
  setCursor(i);
  const st = stage.value!;
  // Past the end of the scroll the browser clamps, which would look like a manual scroll.
  target = Math.max(0, Math.min(st.scrollHeight - st.clientHeight, wordY(i) - eyeY()));
}

let starts = 0;
async function startListening() {
  const run = ++starts;
  moved();
  voiceNote.value = 'starting…';
  try {
    const l = await listen(heard, (s, stopped) => { voiceNote.value = s; if (stopped) { listener = null; setPlaying(false); } });
    if (run === starts && playing.value && prefs.voice) listener = l; else l.stop();   // paused while it started
  } catch (e) {
    voiceNote.value = (e as Error).message;
    setPlaying(false);
  }
}
function stopListening() {
  starts++;
  listener?.stop();
  listener = null;
  target = null;
  if (voiceNote.value.startsWith('listening')) voiceNote.value = '';
}

function tick(now: number) {
  const dt = last ? Math.min((now - last) / 1000, 0.1) : 0;   // a stalled frame must not leap
  last = now;
  const st = stage.value;
  if (st && playing.value) {
    if (prefs.voice) {
      // Keep moving at the base speed, faster when the speaker is ahead of the reading line and
      // slower, down to a stop, when behind. Only a restart well over a line back scrolls up.
      const base = pxPerSec();
      let want = base;
      if (target !== null) {
        const off = target - pos;
        const back = off < -prefs.size * 1.5 || (velocity < 0 && off < 0);   // and once going back, settle there
        want = back ? off * 0.8 : Math.max(0, Math.min(base + off * 0.5, 600));
      }
      velocity += (want - velocity) * Math.min(1, dt * 2);
      pos = Math.max(0, Math.min(st.scrollHeight - st.clientHeight, pos + velocity * dt));
      st.scrollTop = pos;
    } else {
      pos += pxPerSec() * dt;
      const max = st.scrollHeight - st.clientHeight;
      if (pos >= max) { pos = max; setPlaying(false); }
      st.scrollTop = pos;
    }
    elapsed += dt * 1000;
  }
  const n = script.value?.slides.length || 0;
  statusLine.value = `slide ${n ? currentSlide() + 1 : 0} of ${n} · ${clock(elapsed)}`;
  raf = requestAnimationFrame(tick);
}

function setPlaying(on: boolean) {
  if (on === playing.value) return;
  playing.value = on;
  last = 0;
  velocity = 0;
  if (prefs.voice) { if (on) void startListening(); else stopListening(); }
}

// Keep manual scrolling (trackpad, wheel) and auto-scroll in step.
function onScroll() {
  if (stage.value && Math.abs(stage.value.scrollTop - pos) > 2) { pos = stage.value.scrollTop; moved(); }
}

function change(key: 'speed' | 'size' | 'width', delta: number) {
  const limits = { speed: [1, 30], size: [24, 140], width: [40, 95] }[key];
  const k = key === 'speed' ? speedKey() : key;
  prefs[k] = Math.max(limits[0], Math.min(limits[1], prefs[k] + delta));
  savePrefs();
}
function toggleMirror() { prefs.mirror = !prefs.mirror; savePrefs(); }
function toggleVoice() {
  const was = playing.value;
  setPlaying(false);
  prefs.voice = !prefs.voice;
  savePrefs();
  if (!prefs.voice) setCursor(-1);
  voiceNote.value = '';
  setPlaying(was);
}
function toggleFullscreen() {
  if (document.fullscreenElement) void document.exitFullscreen?.();
  else void document.documentElement.requestFullscreen?.().catch(() => {});
}
function leave() {
  if (document.fullscreenElement) void document.exitFullscreen?.();
  void router.push(`/topic/${props.id}`);
}

function onKey(e: KeyboardEvent) {
  const k = e.key;
  if (k === ' ' || k === 'b' || k === 'B' || k === '.') setPlaying(!playing.value);
  else if (k === 'ArrowUp') change('speed', 1);
  else if (k === 'ArrowDown') change('speed', -1);
  else if (k === 'ArrowRight' || k === 'PageDown') goToSlide(currentSlide() + 1);
  else if (k === 'ArrowLeft' || k === 'PageUp') goToSlide(currentSlide() - 1);
  else if (k === '+' || k === '=') change('size', 4);
  else if (k === '-' || k === '_') change('size', -4);
  else if (k === 'Home') { pos = 0; if (stage.value) stage.value.scrollTop = 0; elapsed = 0; moved(); }
  else if (k === 'm' || k === 'M') toggleMirror();
  else if (k === 'v' || k === 'V') toggleVoice();
  else if (k === 'f' || k === 'F') toggleFullscreen();
  else if (k === 'Escape' && !document.fullscreenElement) leave();
  else return;
  e.preventDefault();
}

onMounted(async () => {
  document.body.classList.add('prompting');
  document.addEventListener('keydown', onKey);
  try {
    const data = await api<TopicResponse>(`/api/topic/${props.id}`);
    script.value = data.show.results?.[0]?.en ?? null;
    if (!script.value) error.value = 'This topic has no topic.md yet.';
    else { await nextTick(); wrapWords(); }
  } catch (e) {
    error.value = `Could not load the script: ${(e as Error).message}`;
  }
  stage.value?.focus();
  raf = requestAnimationFrame(tick);
});

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  stopListening();
  document.removeEventListener('keydown', onKey);
  document.body.classList.remove('prompting');
  if (document.fullscreenElement) void document.exitFullscreen?.();
});
</script>

<template>
  <div>
    <div ref="stage" class="prompter" tabindex="-1" @scroll="onScroll">
      <div ref="textEl" class="prompter-text"
        :style="{ fontSize: `${prefs.size}px`, width: `${prefs.width}vw`, transform: prefs.mirror ? 'scaleX(-1)' : '' }">
        <p v-if="error">{{ error }}</p>
        <template v-else-if="script">
          <div class="prompter-title">{{ id }} · {{ script.front?.title || '' }}</div>
          <template v-for="s in script.slides" :key="s.index">
            <div class="prompter-divider">Slide {{ s.index }}{{ s.title ? ` · ${s.title}` : '' }}</div>
            <!-- Escaped above; only ** and * become markup. -->
            <p v-for="(p, i) in (s.paragraphs?.length ? s.paragraphs : [s.narration || ''])" :key="i" v-html="inline(p)"></p>
          </template>
          <div class="prompter-end">— end —</div>
        </template>
      </div>
    </div>
    <div class="prompter-eyeline"><span>▶</span><span>◀</span></div>
    <!-- Hand focus back to the stage after any bar click, or Space re-presses the focused button on key-up. -->
    <div class="prompter-bar" @click="stage?.focus()">
      <q-toolbar class="bg-grey-10 text-grey-4 q-gutter-sm" style="flex-wrap: wrap">
        <q-btn :color="playing ? 'amber-8' : 'grey-8'" unelevated no-caps :icon="playing ? 'pause' : 'play_arrow'" :label="playing ? 'Pause' : 'Play'" @click="setPlaying(!playing)"><q-tooltip>Space</q-tooltip></q-btn>
        <q-btn dense no-caps :flat="!prefs.voice" :color="prefs.voice ? 'amber-8' : undefined" icon="mic" label="Voice" @click="toggleVoice()"><q-tooltip>V · follow your voice instead of a set speed</q-tooltip></q-btn>
        <span v-for="k in (['speed', 'size', 'width'] as const)" :key="k" class="row items-center gap-xs">
          <span class="text-caption text-capitalize">{{ k }}</span>
          <q-btn dense flat round icon="remove" @click="change(k, k === 'speed' ? -1 : k === 'size' ? -4 : -5)" />
          <b>{{ prefs[k === 'speed' ? speedKey() : k] }}{{ k === 'width' ? '%' : '' }}</b>
          <q-btn dense flat round icon="add" @click="change(k, k === 'speed' ? 1 : k === 'size' ? 4 : 5)" />
        </span>
        <q-btn dense no-caps :flat="!prefs.mirror" :color="prefs.mirror ? 'amber-8' : undefined" label="Mirror" @click="toggleMirror()"><q-tooltip>M</q-tooltip></q-btn>
        <q-btn dense flat no-caps icon="fullscreen" label="Full screen" @click="toggleFullscreen"><q-tooltip>F</q-tooltip></q-btn>
        <q-space />
        <span v-if="prefs.voice && voiceNote" class="text-caption text-amber-6">{{ voiceNote }}</span>
        <span class="text-caption">{{ statusLine }}</span>
        <q-btn dense flat no-caps icon="close" label="Close" @click="leave"><q-tooltip>Esc</q-tooltip></q-btn>
      </q-toolbar>
    </div>
  </div>
</template>
