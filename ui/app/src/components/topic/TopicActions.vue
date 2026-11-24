<script setup lang="ts">
// The topic's pipeline in both languages. English forks after validate into two tracks: the video
// track (the edited video, render, bumpers, compose, package) and, below it, the subtitle track
// (the editor's SRT, cues, subtitles, ready for translation), because the subtitle side needs
// neither render nor the video. Mandarin is the row below, branching off the subtitle track.
// Each step shows whether bcn says it is done, out of date, failed or not run, with the next step
// marked. Clicking a step runs it in its row's language.
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { StepState } from '@beacon/shared';
import { fmtAgo, LANG_NAME, STEP_HELP } from '@/format';
import { bumperStatus } from '@/progress';
import { useBeacon } from '@/stores/beacon';
import { TOPIC } from './context';

type Item =
  | { kind: 'step'; name: string; optional?: boolean }
  | { kind: 'wait'; name: string; label: string; icon: string; to?: (id: string) => string }
  | { kind: 'bumpers' };

// The order bcn works through (tooling/bcn/state.py). The waits are what a person does
// between steps: the editor delivers the SRT and the video, the translator returns the Mandarin.
// bumpers is its own kind, not a 'step': bcn tracks it as a tool with its own artifacts
// (intro/outro card + video), not a pipeline step with a StepState, so it needs compose's
// --no-bumpers to have something to draw from before compose runs.
// head runs first; each lane then follows it, one grid row per lane.
const FLOW: Record<'en' | 'zh', { head: Item[]; lanes: Item[][] }> = {
  en: {
    head: [{ kind: 'step', name: 'validate' }],
    lanes: [
      // render and bumpers need only validate, not the video; nothing here waits on the one
      // before it except compose, which needs all three (and cues). The video leads because
      // it is the editor's delivery, the long wait on this track.
      [{ kind: 'wait', name: 'video', label: 'Video in', icon: 'videocam' },
        { kind: 'step', name: 'render' },
        { kind: 'bumpers' },
        { kind: 'step', name: 'compose', optional: true }, { kind: 'step', name: 'package' }],
      [{ kind: 'wait', name: 'subtitles_in', label: 'Subtitles in', icon: 'closed_caption' },
        { kind: 'step', name: 'cues' }, { kind: 'step', name: 'subtitles' },
        { kind: 'wait', name: 'translation_ready', label: 'Ready for translation', icon: 'translate',
          to: (id) => `/caption/${id}?lang=en` }],
    ],
  },
  zh: {
    head: [],
    lanes: [[
      { kind: 'wait', name: 'translation', label: 'Translation', icon: 'translate' },
      { kind: 'step', name: 'validate' }, { kind: 'step', name: 'subtitles' }, { kind: 'step', name: 'render' },
      { kind: 'bumpers' },
      { kind: 'step', name: 'compose', optional: true }, { kind: 'step', name: 'package' },
    ]],
  },
};
// The editor's two deliveries, each its own wait: which one bcn is waiting on, and the file
// that ends the wait (status artifacts carry an exists flag for each).
const ARRIVAL: Record<string, { next: string; key: string }> = {
  subtitles_in: { next: 'await_subtitles', key: 'edit/master.srt' },
  video: { next: 'await_video', key: 'edit/master.mp4' },
};
const WAIT_HELP: Record<string, string> = {
  subtitles_in: 'The editor delivers the English subtitles, edit/master.srt. Upload them in the Video pane; nothing to run here. '
    + 'They are all that cues, subtitles and sending for translation need: no render, no video.',
  video: 'The editor delivers the edited video, edit/master.mp4. Upload it in the Video pane; nothing to run here. '
    + 'Compose needs it; once it is in, cues runs again to check the subtitles against it.',
  translation: 'The English SRT and slides go to the translator from the Translation page. The returned files come back either there, '
    + 'or dropped straight into the Slides pane above if the translator sent them loose under the wrong names. '
    + 'A topic can be sent once its English subtitles step is done, so any corrected mishearings reach the translator.',
  translation_ready: 'Whether the English subtitles are fit to hand to a translator: validated, through bcn subtitles, and with '
    + 'every suspected mis-transcription reviewed. Click to open the Caption Editor and fix what is outstanding.',
};

const LANGS = ['en', 'zh'] as const;

const emit = defineEmits<{ verify: [] }>();
const t = inject(TOPIC)!;
const beacon = useBeacon();
const force = ref(false);
// Compose makes the full, delivered version unless draft is ticked; no bumpers is drafts only
// (bcn compose refuses --no-bumpers without --draft).
const noBumpers = ref(false);
const draftMode = ref(false);
const selectedLayouts = ref<string[]>([]);
let seededLayouts = false;

// The programme's own active layout set ([compose].default_layouts, read off bcn status —
// not the full [compose.layouts] catalog of styles merely available to pick from). Seeded
// into selectedLayouts once, the first time it loads, so it doesn't stomp a selection already
// in progress on every status poll.
const activeLayouts = computed(() => t.status.value?.active_layouts ?? []);
watch(activeLayouts, (v) => {
  if (!seededLayouts && v.length) {
    selectedLayouts.value = v.map((l) => l.name).filter((n): n is string => !!n);
    seededLayouts = true;
  }
}, { immediate: true });

const stateOf = (lang: 'en' | 'zh') => t.status.value?.[lang] ?? null;

function view(item: Item, lang: 'en' | 'zh') {
  const s = stateOf(lang);
  const next = s?.next ?? null;
  if (item.kind === 'wait') {
    if (item.name === 'translation_ready') {
      const subs = t.status.value?.en?.steps?.subtitles;
      const unreviewed = t.status.value?.unreviewed_mistranscriptions ?? 0;
      const zh = t.status.value?.zh;
      // Once the topic has left 'not_sent', the export gate already did its job at send time -
      // zh.next now tracks Mandarin's own post-return pipeline (validate/subtitles/render/...),
      // which never again equals 'translation_export', so that check alone would leave this tile
      // permanently stuck on "needs another look" for every topic whose translation came back.
      const sent = !!zh && zh.stage !== 'not_sent';
      const approved = zh?.next === 'translation_export' || sent;
      const status = !subs?.ok || !subs?.fresh ? 'todo' : approved ? 'done' : 'warn';
      const ICON = { done: 'check_circle', warn: 'rate_review', todo: 'radio_button_unchecked' } as const;
      const COLOR = { done: 'positive', warn: 'warning', todo: 'grey-6' } as const;
      const CAP = { done: sent ? 'sent' : 'ready to send',
        warn: unreviewed ? `${unreviewed} mis-transcription${unreviewed > 1 ? 's' : ''} to review` : 'needs another look',
        todo: 'after English subtitles' };
      return { cls: ['wait', status], icon: ICON[status], color: COLOR[status], cap: CAP[status],
        clickable: status !== 'todo', tip: WAIT_HELP.translation_ready };
    }
    const arrival = ARRIVAL[item.name];
    const current = arrival ? next === arrival.next
      : next === 'translation_export' || next === 'await_translation';
    const reached = arrival
      ? !!t.status.value?.artifacts?.some((a) => a.key === arrival.key && a.exists)
      : !!s && s.stage_index >= s.stages.indexOf('returned');
    const cap = current ? (next === 'translation_export' ? 'ready to send' : next === 'await_translation' ? 'with the translator' : 'waiting for the editor')
      : reached ? 'arrived' : item.name === 'translation' ? 'after English subtitles' : 'not yet';
    return { cls: ['wait', { current }], icon: reached && !current ? 'check_circle' : item.icon, color: current ? 'white' : reached ? 'positive' : undefined,
      cap, tip: WAIT_HELP[item.name] };
  }
  if (item.kind === 'bumpers') {
    // Not a pipeline step on the backend (no StepState): derived from the bumper_card/bumper
    // artifacts themselves, the same data SlidesPane reads to show the intro/outro thumbnails.
    // Reads topic.zh.md for the Mandarin title (bumpers.py: require_input(t, src, lang)), so
    // it is gated the same as the real Mandarin steps until translation has been returned.
    const waitingForTranslation = lang === 'zh' && !!s && s.stage_index < s.stages.indexOf('returned');
    // The module grid's rule too (progress.ts): a size not made yet counts as out of date.
    const status = ({ ok: 'done', stale: 'stale', none: 'todo' } as const)[bumperStatus({ artifacts: t.status.value?.artifacts || [] }, lang)];
    const ICON = { done: 'check_circle', stale: 'update', todo: 'radio_button_unchecked' } as const;
    const COLOR = { done: 'positive', stale: 'warning', todo: 'grey-6' } as const;
    const CAP = { done: '', stale: 'out of date', todo: 'optional' };
    return {
      cls: ['step-btn', status, { optional: true, disabled: waitingForTranslation }],
      icon: ICON[status], color: COLOR[status], cap: waitingForTranslation ? 'needs translation' : CAP[status],
      disable: waitingForTranslation,
      tip: `${lang === 'zh' ? 'Mandarin. ' : ''}${STEP_HELP.bumpers} Needed before compose draws on them; compose can also run with --no-bumpers.`
        + (waitingForTranslation ? ' Needs topic.zh.md back from the translator first.' : ''),
    };
  }
  const st: StepState | undefined = s?.steps?.[item.name];
  const current = next === item.name;
  // Every Mandarin step reads topic.zh.md / the Mandarin SRT, which do not exist until
  // translation has been returned (tooling/bcn/state.py: validate/subtitles/render take
  // zsrc/zsrt as inputs). Running one before then would just fail, so it is disabled here.
  const waitingForTranslation = lang === 'zh' && !!s && s.stage_index < s.stages.indexOf('returned');
  // Compose can be run as a --draft preview (ok, fresh) without being "done": package needs
  // a delivered (non-draft) run, so `next` keeps pointing back at compose until one exists
  // (Step.delivered in state.py). Without this, a fresh draft run would show "done" right
  // next to "next: compose", which looks like a contradiction rather than "preview only."
  const draftOnly = item.name === 'compose' && !!st?.exists && st.ok && st.fresh && !!st.draft;
  // cues failed on slide timings alone: the subtitle track and translation go ahead (bcn
  // subtitles ignores those errors), only a full compose waits, so this is amber, not red.
  const toPlace = st?.slides_to_place ?? null;
  const slides = toPlace !== null && !!st?.fresh;
  const status = !st?.exists ? 'todo' : slides ? 'slides' : !st.ok ? 'failed' : draftOnly ? 'draft' : st.fresh ? 'done' : 'stale';
  const ICON = { done: 'check_circle', stale: 'update', failed: 'error', todo: 'radio_button_unchecked', draft: 'visibility', slides: 'pin_drop' } as const;
  const COLOR = { done: 'positive', stale: 'warning', failed: 'negative', todo: 'grey-6', draft: 'warning', slides: 'warning' } as const;
  // A finished step says when it ran, not "done": the green tick already says that.
  const CAP = { done: st?.time ? fmtAgo(st.time, beacon.now) : '', stale: 'out of date', failed: 'failed',
    todo: item.optional ? 'optional check' : 'not run', draft: `draft only${st?.time ? ` · ${fmtAgo(st.time, beacon.now)}` : ''}`,
    slides: toPlace ? `${toPlace} slide${toPlace > 1 ? 's' : ''} to place` : 'slide timings to fix' };
  return {
    cls: ['step-btn', status, { current, optional: item.optional, disabled: waitingForTranslation }],
    icon: current && status === 'todo' ? 'play_circle' : ICON[status],
    color: current ? 'white' : COLOR[status],
    cap: waitingForTranslation ? 'needs translation' : CAP[status],
    disable: waitingForTranslation,
    tip: `${lang === 'zh' ? 'Mandarin. ' : ''}${STEP_HELP[item.name]}${current ? ' This is the next step for this topic.' : ''}`
      + (waitingForTranslation ? ' Needs topic.zh.md and the Mandarin SRT back from the translator first.' : '')
      + (draftOnly ? ' The last run was a draft preview, not delivered — run it without --draft before package can use it.' : '')
      + (slides ? ' A slide break needs a person: watch a draft compose and set it in the Video pane. Subtitles and '
        + 'translation can go ahead meanwhile; a full compose waits for it.' : '')
      + (item.name === 'compose' && lang === 'en' ? ' A full compose also needs cues to have passed, with the video in; '
        + 'a draft accepts a best-guess cue sheet.' : ''),
  };
}

// Each row is a grid: the label, then head, then every lane on its own grid row with an arrow
// before each item. A lane after a head starts with an arrow out of the head (the fork).
type Cell = { key: string; row: string; col: number } & (
  | { kind: 'label' } | { kind: 'arrow' } | { kind: 'item'; item: Item; v: ReturnType<typeof view> });
const itemKey = (item: Item) => (item.kind === 'bumpers' ? 'bumpers' : item.name);
const rows = computed(() => LANGS.map((lang) => {
  const { head, lanes } = FLOW[lang];
  const span = `1 / span ${lanes.length}`;
  const cells: Cell[] = [{ key: 'label', kind: 'label', row: span, col: 1 }];
  let col = 2;
  head.forEach((item, i) => {
    if (i > 0) cells.push({ key: `h-arrow-${i}`, kind: 'arrow', row: span, col: col++ });
    cells.push({ key: itemKey(item), kind: 'item', item, v: view(item, lang), row: span, col: col++ });
  });
  lanes.forEach((lane, li) => {
    let c = col;
    lane.forEach((item, i) => {
      if (i > 0 || head.length) cells.push({ key: `l${li}-arrow-${i}`, kind: 'arrow', row: String(li + 1), col: c++ });
      cells.push({ key: itemKey(item), kind: 'item', item, v: view(item, lang), row: String(li + 1), col: c++ });
    });
  });
  return { lang, complete: !!stateOf(lang)?.complete, cells };
}));

// Two connectors drawn over the rows: English "Ready for translation", at the end of the subtitle
// track, down to the start of the Mandarin row (that is when a topic can go out for translation);
// and the end of the Mandarin row round the right-hand side and into English package, which
// delivers both languages together. Measured from the blocks themselves, so they follow the
// layout at any width.
const wrap = ref<HTMLElement | null>(null);
const links = ref<{ d: string; key: string }[]>([]);
const size = ref({ w: 0, h: 0 });
function measure() {
  const w = wrap.value;
  if (!w) return;
  const box = w.getBoundingClientRect();
  const at = (key: string) => {
    const el = w.querySelector<HTMLElement>(`[data-step="${key}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { l: r.left - box.left, r: r.right - box.left, t: r.top - box.top, b: r.bottom - box.top };
  };
  const subs = at('en-translation_ready'), pkg = at('en-package');
  const first = at('zh-translation'), last = at('zh-package');
  size.value = { w: w.scrollWidth, h: w.scrollHeight };
  if (!subs || !pkg || !first || !last) { links.value = []; return; }
  const gapTop = Math.max(subs.b, pkg.b);
  const gapBottom = Math.min(first.t, last.t);
  const y1 = gapTop + (gapBottom - gapTop) * 0.5;
  const outX = subs.l + (subs.r - subs.l) / 2;       // straight down out of the tile
  const inX = first.l + 26;
  // Package sits on the upper (video) track, so the way back goes round the right of
  // everything and enters it from the side, never crossing the subtitle track.
  const back = Math.max(last.r, pkg.r, subs.r) + 14;
  const pkgY = pkg.t + (pkg.b - pkg.t) / 2;
  links.value = [
    { key: 'out', d: `M ${outX} ${subs.b + 1} V ${y1} H ${inX} V ${first.t - 2}` },
    { key: 'back', d: `M ${last.r + 1} ${last.t + (last.b - last.t) / 2} H ${back} V ${pkgY} H ${pkg.r + 2}` },
  ];
}
let ro: ResizeObserver | null = null;
onMounted(() => {
  ro = new ResizeObserver(() => measure());
  if (wrap.value) ro.observe(wrap.value);
  nextTick(measure);
});
onBeforeUnmount(() => ro?.disconnect());
watch(rows, () => nextTick(measure));

async function run(step: string, lang: 'en' | 'zh') {
  const args: Record<string, string | boolean | string[]> = { lang: step === 'cues' ? 'en' : lang };
  if (force.value) args.force = true;
  if (step === 'compose' && draftMode.value) {
    args.draft = true;
    if (noBumpers.value) args.no_bumpers = true;
  }
  if (step === 'compose') args.layout = selectedLayouts.value;
  if (step === 'qa') delete args.lang;
  await beacon.runJob(step, [t.rel], args);
}

// Intro/outro makes files the Slides pane links to, so it waits for them and says where they are.
async function bumpers(lang: 'en' | 'zh') {
  const job = await beacon.runJob('bumpers', [t.rel], { lang, ...(force.value ? { force: true } : {}) });
  const result = await beacon.awaitJob(job.id);
  await t.reload();
  if (result.state === 'done') beacon.toast('Intro and outro ready: see the top of the Slides pane.');
  else beacon.toast('The intro and outro could not be made; see Jobs.', true);
}
</script>

<template>
  <q-card flat bordered class="q-mb-md">
    <q-card-section class="q-pb-sm">
      <div class="row items-center q-mb-sm">
        <div class="text-subtitle2">Steps</div>
        <q-space />
        <span class="text-caption text-grey-7 q-mr-sm">Click a step to run it in that row's language</span>
        <q-btn flat dense no-caps icon="fact_check" label="qa" aria-label="Run qa" @click="run('qa', 'en')">
          <q-tooltip max-width="340px">{{ STEP_HELP.qa }}</q-tooltip>
        </q-btn>
      </div>
      <div class="stepscroll">
        <div ref="wrap" class="stepwrap">
          <div v-for="r in rows" :key="r.lang" :class="['stepflow', 'steprow', 'stepgrid', r.lang]">
            <template v-for="c in r.cells" :key="r.lang + c.key">
              <div v-if="c.kind === 'label'" class="row-label" :style="{ gridRow: c.row, gridColumn: c.col }">
                {{ r.lang.toUpperCase() }}
                <q-icon v-if="r.complete" name="task_alt" color="positive" size="14px"><q-tooltip>{{ LANG_NAME[r.lang] }} complete</q-tooltip></q-icon>
              </div>
              <q-icon v-else-if="c.kind === 'arrow'" name="arrow_forward" size="18px" class="arrow" :style="{ gridRow: c.row, gridColumn: c.col }" />
              <router-link v-else-if="c.item.kind === 'wait' && c.item.to && c.v.clickable" :to="c.item.to(t.id)"
                :class="['step', 'wait', 'link', c.v.cls[1]]" :data-step="`${r.lang}-${c.item.name}`" :style="{ gridRow: c.row, gridColumn: c.col }">
                <div class="name"><q-icon :name="c.v.icon" :color="c.v.color" size="16px" />{{ c.item.label }}</div>
                <div class="cap">{{ c.v.cap }}</div>
                <q-tooltip max-width="320px">{{ c.v.tip }}</q-tooltip>
              </router-link>
              <div v-else-if="c.item.kind === 'wait'" :class="['step', ...c.v.cls]" :data-step="`${r.lang}-${c.item.name}`" :style="{ gridRow: c.row, gridColumn: c.col }">
                <div class="name"><q-icon :name="c.v.icon" :color="c.v.color" size="16px" />{{ c.item.label }}</div>
                <div class="cap">{{ c.v.cap }}</div>
                <q-tooltip max-width="320px">{{ c.v.tip }}</q-tooltip>
              </div>
              <q-btn v-else-if="c.item.kind === 'bumpers'" no-caps flat :class="['step', ...c.v.cls]" :data-step="`${r.lang}-bumpers`" :disable="c.v.disable"
                :style="{ gridRow: c.row, gridColumn: c.col }" :aria-label="`Make intro/outro (${LANG_NAME[r.lang]})`" @click="bumpers(r.lang)">
                <div class="name"><q-icon :name="c.v.icon" :color="c.v.color" size="16px" />bumpers</div>
                <div class="cap">{{ c.v.cap }}</div>
                <q-tooltip max-width="340px">{{ c.v.tip }}</q-tooltip>
              </q-btn>
              <q-btn v-else no-caps flat :class="['step', ...c.v.cls]" :data-step="`${r.lang}-${c.item.name}`" :disable="c.v.disable"
                :style="{ gridRow: c.row, gridColumn: c.col }" :aria-label="`Run ${c.item.name} (${LANG_NAME[r.lang]})`" @click="run(c.item.name, r.lang)">
                <div class="name"><q-icon :name="c.v.icon" :color="c.v.color" size="16px" />{{ c.item.name }}</div>
                <div class="cap">{{ c.v.cap }}</div>
                <q-tooltip max-width="340px">{{ c.v.tip }}</q-tooltip>
              </q-btn>
            </template>
          </div>
          <svg class="steplinks" :width="size.w" :height="size.h" aria-hidden="true">
            <defs>
              <marker id="steplink-head" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" class="head" />
              </marker>
            </defs>
            <path v-for="l in links" :key="l.key" :d="l.d" class="link" marker-end="url(#steplink-head)" />
          </svg>
        </div>
      </div>
    </q-card-section>
    <q-separator />
    <q-card-section class="row items-center gap-sm q-py-sm">
      <q-checkbox v-model="force" dense label="force re-run">
        <q-tooltip max-width="320px">Rebuild even if the result is already up to date. Normally a step is skipped when nothing it depends on has changed.</q-tooltip>
      </q-checkbox>
      <q-checkbox v-model="draftMode" dense label="draft">
        <q-tooltip max-width="320px">Make a quick draft instead (small, watermarked, subtitles burned in) to check that the slides change at the right moments. Much faster than a full compose; never delivered.</q-tooltip>
      </q-checkbox>
      <q-checkbox v-model="noBumpers" dense label="no bumpers" :disable="!draftMode">
        <q-tooltip max-width="320px">Drafts only: leave the intro and outro off the draft. Quicker, and the player's times then match the cue sheet exactly. A full compose always has its intro and outro.</q-tooltip>
      </q-checkbox>
      <q-separator vertical class="q-mx-xs" />
      <span class="text-caption text-grey-7">layouts</span>
      <q-checkbox v-for="l in activeLayouts" :key="l.name" dense :label="`${l.name} (${l.width}×${l.height})`"
        :model-value="!!l.name && selectedLayouts.includes(l.name)"
        @update:model-value="(v) => { if (!l.name) return; selectedLayouts = v ? [...selectedLayouts, l.name] : selectedLayouts.filter((x) => x !== l.name); }">
        <q-tooltip max-width="320px">Which of the programme's active output(s) compose builds this run. Pick more than one to get, e.g., a widescreen cut and a vertical cut from the same recording in one run. The active set itself is a programme-wide setting ([compose].default_layouts in programme.toml).</q-tooltip>
      </q-checkbox>
      <q-space />
      <q-btn flat dense no-caps icon="verified" label="Verify files" @click="emit('verify')">
        <q-tooltip max-width="320px">Open each of this topic's files to confirm it is what it claims (not empty, not corrupt), and that delivered files match their checksums. Results show in the Artefacts table.</q-tooltip>
      </q-btn>
    </q-card-section>
  </q-card>
</template>
