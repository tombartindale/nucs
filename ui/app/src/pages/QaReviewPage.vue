<script setup lang="ts">
// One Mandarin QA item: the composed video (with its Mandarin subtitles) or the activity,
// beside its verdict and comment thread. A comment can point at a moment in the video or a
// line of the activity; clicking that anchor jumps there.
import { computed, nextTick, ref, shallowRef, watch } from 'vue';
import { useRouter } from 'vue-router';
import type { QaComment, QaCommentKind, QaItemResponse, QaStatus } from '@beacon/shared';
import { api, fileUrl } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { confirm } from '@/composables/confirm';
import { useQaFilter } from '@/composables/qaFilter';
import { fmtAgo, fmtTime, QA_STATUS, topicPath } from '@/format';
import { useBeacon } from '@/stores/beacon';

const props = defineProps<{ item: string; startAt: number | null; line: number | null }>();
const beacon = useBeacon();
const router = useRouter();
const { items: filtered } = useQaFilter();

const data = shallowRef<QaItemResponse | null>(null);
const error = ref<string | null>(null);
const busy = ref(false);
const qa = computed(() => data.value?.item ?? null);
const isVideo = computed(() => qa.value?.type === 'video');

async function load() {
  try {
    data.value = await api<QaItemResponse>(`/api/qa/item/${encodeURIComponent(props.item)}`);
    error.value = null;
  } catch (e) {
    error.value = (e as Error).message;
  }
}
// Someone else commenting or changing the verdict shows up here without a reload.
watch(() => [props.item, beacon.qaTick], () => { void load(); }, { immediate: true });

async function send<T extends QaItemResponse>(path: string, method: string, body?: unknown): Promise<T | null> {
  busy.value = true;
  try {
    const r = await api<T>(path, { method, body });
    data.value = r;
    return r;
  } catch (e) {
    beacon.toast((e as Error).message, true);
    return null;
  } finally {
    busy.value = false;
  }
}
const itemPath = computed(() => `/api/qa/item/${encodeURIComponent(props.item)}`);

// -- the viewer ---------------------------------------------------------------------------------
const video = ref<HTMLVideoElement | null>(null);
const time = ref(0);
let seeked = false;
function onMeta() {
  if (!seeked && props.startAt !== null && video.value) { video.value.currentTime = props.startAt; seeked = true; }
  // Mandarin subtitles on by default: they are half of what is being reviewed.
  const tracks = video.value?.textTracks;
  if (tracks?.length) tracks[0].mode = 'showing';
}
function seek(t: number) {
  if (!video.value) return;
  video.value.currentTime = t;
  void video.value.play().catch(() => {});
}

const text = ref<string | null>(null);
const lines = computed(() => (text.value ?? '').replace(/\r\n/g, '\n').split('\n'));
const selectedLine = ref<number | null>(null);
async function loadText() {
  text.value = null;
  if (!qa.value || qa.value.type !== 'activity') return;
  try {
    const res = await fetch(fileUrl(qa.value.path, qa.value.mtime, 'view=1'));
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    text.value = await res.text();
  } catch (e) {
    error.value = `Could not load ${qa.value.path}: ${(e as Error).message}`;
    return;
  }
  if (props.line) { await nextTick(); scrollTo(props.line); }
}
watch(() => (qa.value ? `${qa.value.path}@${qa.value.mtime}` : ''), () => { void loadText(); });
function scrollTo(n: number) {
  selectedLine.value = n;
  document.getElementById(`L${n}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
}
const commentedLines = computed(() => {
  const m = new Map<number, number>();
  for (const c of data.value?.comments || []) if (c.anchorLine && !c.resolved) m.set(c.anchorLine, (m.get(c.anchorLine) || 0) + 1);
  return m;
});
const videoMarks = computed(() => (data.value?.comments || []).filter((c) => c.anchorT !== null && !c.resolved));
const duration = ref(0);

function jump(c: QaComment) {
  if (c.anchorT !== null) seek(c.anchorT);
  else if (c.anchorLine !== null) scrollTo(c.anchorLine);
}

// -- verdict ------------------------------------------------------------------------------------
async function setStatus(status: QaStatus) {
  if (!qa.value || status === qa.value.status && !qa.value.changedSinceReview) return;
  const open = data.value?.comments.filter((c) => !c.resolved).length || 0;
  if (status === 'approved' && open) {
    const ok = await confirm({ title: 'Approve with open comments?', ok: 'Approve',
      lines: [`${open} comment${open === 1 ? ' is' : 's are'} not resolved yet. Approve anyway?`] });
    if (!ok) return;
  }
  await send(`${itemPath.value}/status`, 'PUT', { status });
}

// -- comments -----------------------------------------------------------------------------------
const draft = ref('');
const kind = ref<QaCommentKind>('comment');
const anchor = ref(true);
const anchorLabel = computed(() => {
  if (isVideo.value) return `at ${fmtTime(time.value)}`;
  return selectedLine.value ? `on line ${selectedLine.value}` : null;
});
async function addComment() {
  const body = draft.value.trim();
  if (!body) return;
  const withAnchor = anchor.value && anchorLabel.value;
  const ok = await send(`${itemPath.value}/comments`, 'POST', {
    body, kind: kind.value,
    anchorT: withAnchor && isVideo.value ? Math.round(time.value * 10) / 10 : null,
    anchorLine: withAnchor && !isVideo.value ? selectedLine.value : null,
  });
  if (ok) draft.value = '';
}
const resolve = (c: QaComment) => send(`/api/qa/comments/${c.id}`, 'PATCH', { resolved: !c.resolved });
async function remove(c: QaComment) {
  if (!await confirm({ title: 'Delete this comment?', ok: 'Delete', danger: true, lines: [c.body.slice(0, 200)] })) return;
  await send(`/api/qa/comments/${c.id}`, 'DELETE');
}
const showResolved = ref(true);
const comments = computed(() => (data.value?.comments || []).filter((c) => showResolved.value || !c.resolved));
const resolvedCount = computed(() => (data.value?.comments || []).filter((c) => c.resolved).length);

// -- navigation ---------------------------------------------------------------------------------
const neighbours = computed(() => {
  const list = filtered.value;
  const i = list.findIndex((x) => x.item === props.item);
  const others = list.filter((x) => x.item !== props.item);
  if (!others.length) return { prev: null, next: null };
  if (i < 0) return { prev: null, next: others[0] };
  return { prev: list[i - 1] ?? null, next: list[i + 1] ?? null };
});
const go = (item: string) => router.push(`/qa/${item}`);
watch(() => props.item, () => { seeked = false; selectedLine.value = null; draft.value = ''; time.value = 0; });

const crumbs = computed(() => [{ label: 'QA', to: '/qa' }, { label: qa.value ? (qa.value.topic ?? `${qa.value.module}-${qa.value.unit}`) : props.item }]);
const sourceLink = computed(() => {
  const q = qa.value;
  if (!q) return null;
  return q.type === 'video' ? `/topic/${q.topic}` : `/doc/${q.path}`;
});
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader :title="qa ? `${qa.title}` : 'QA'" :crumbs="crumbs"
      :sub="qa ? `${qa.type === 'video' ? 'Composed Mandarin video' : 'Mandarin activity'} · ${qa.topic ? topicPath(qa.topic) : `${qa.module}/${qa.unit}`}` : ''">
      <q-btn flat dense no-caps icon="chevron_left" label="Previous" :disable="!neighbours.prev" @click="neighbours.prev && go(neighbours.prev.item)" />
      <q-btn flat dense no-caps icon-right="chevron_right" label="Next" :disable="!neighbours.next" @click="neighbours.next && go(neighbours.next.item)" />
      <q-btn v-if="sourceLink" outline no-caps icon="open_in_new" :label="qa?.type === 'video' ? 'Topic' : 'Document'" :to="sourceLink" />
    </PageHeader>

    <q-banner v-if="error" class="bg-negative text-white q-mb-md" rounded>{{ error }}</q-banner>

    <div v-if="qa" class="row q-col-gutter-md">
      <!-- the viewer -->
      <div class="col-12 col-md-8">
        <q-banner v-if="qa.changedSinceReview" dense rounded class="bg-orange-2 q-mb-sm">
          <template #avatar><q-icon name="update" /></template>
          This file has changed since {{ qa.statusBy || 'someone' }} marked it {{ QA_STATUS[qa.status].label.toLowerCase() }}. Look again and give a new verdict.
        </q-banner>
        <q-banner v-if="qa.stale" dense rounded class="bg-orange-2 q-mb-sm">
          <template #avatar><q-icon name="warning" /></template>
          This video is older than its sources: it needs composing again, so what you see may not be final.
        </q-banner>

        <q-card v-if="qa.type === 'video'" flat bordered>
          <video :key="qa.path" ref="video" class="qa-video" :src="fileUrl(qa.path, qa.mtime)" controls preload="metadata" playsinline
            @loadedmetadata="onMeta(); duration = video?.duration || 0" @timeupdate="time = video?.currentTime || 0">
            <track v-if="qa.srt" kind="subtitles" srclang="zh" label="中文" default :src="fileUrl(qa.srt, qa.mtime, 'format=vtt')">
          </video>
          <div v-if="duration && videoMarks.length" class="qa-marks">
            <span v-for="c in videoMarks" :key="c.id" class="qa-mark" :class="`mark-${c.kind}`"
              :style="{ left: `${(100 * c.anchorT!) / duration}%` }" @click="jump(c)">
              <q-tooltip>{{ fmtTime(c.anchorT) }} · {{ c.body.slice(0, 120) }}</q-tooltip>
            </span>
          </div>
          <div class="row items-center q-px-md q-py-sm text-caption text-grey-7 gap-sm">
            <span>{{ fmtTime(time) }}</span>
            <q-space />
            <span v-if="!qa.srt" class="text-warning">No Mandarin subtitle file found beside this video.</span>
            <q-btn flat dense no-caps size="sm" icon="download" label="Download" :href="fileUrl(qa.path, qa.mtime, 'download=1')" />
          </div>
        </q-card>

        <q-card v-else flat bordered class="qa-doc">
          <div v-if="text === null" class="q-pa-md text-grey-7">Loading…</div>
          <table v-else class="doclines">
            <tbody>
              <tr v-for="(l, i) in lines" :id="`L${i + 1}`" :key="i" class="qa-line"
                :class="{ selected: selectedLine === i + 1, commented: commentedLines.has(i + 1) }"
                @click="selectedLine = selectedLine === i + 1 ? null : i + 1">
                <td class="ln">{{ i + 1 }}</td>
                <td>{{ l || ' ' }}</td>
                <td class="cm"><q-icon v-if="commentedLines.has(i + 1)" name="chat_bubble" size="14px" color="warning" /></td>
              </tr>
            </tbody>
          </table>
        </q-card>
        <div v-if="qa.type === 'activity'" class="text-caption text-grey-7 q-mt-xs">Click a line to attach your comment to it.</div>
      </div>

      <!-- verdict and comments -->
      <div class="col-12 col-md-4">
        <q-card flat bordered class="q-mb-md">
          <q-card-section class="q-pb-sm">
            <div class="row items-center gap-sm">
              <div class="text-subtitle2">QA status</div>
              <StateChip :kind="QA_STATUS[qa.status].kind" :label="QA_STATUS[qa.status].label" :icon="QA_STATUS[qa.status].icon" />
            </div>
            <div v-if="qa.statusAt" class="text-caption text-grey-7 q-mt-xs">by {{ qa.statusBy }} · {{ fmtAgo(qa.statusAt, beacon.now) }}</div>
          </q-card-section>
          <q-card-actions class="q-pt-none">
            <q-btn unelevated no-caps color="positive" icon="check" label="Approve" :loading="busy"
              :outline="qa.status !== 'approved'" @click="setStatus('approved')" />
            <q-btn unelevated no-caps color="warning" icon="edit_note" label="Changes needed" :loading="busy"
              :outline="qa.status !== 'changes_needed'" @click="setStatus('changes_needed')" />
            <q-btn v-if="qa.status !== 'to_review'" flat dense no-caps size="sm" label="Reset" :disable="busy" @click="setStatus('to_review')" />
          </q-card-actions>
        </q-card>

        <q-card flat bordered>
          <q-card-section class="row items-center q-pb-none">
            <div class="text-subtitle2">Comments</div>
            <q-space />
            <q-toggle v-if="resolvedCount" v-model="showResolved" dense size="sm" :label="`Show resolved (${resolvedCount})`" class="text-caption" />
          </q-card-section>

          <q-list separator class="qa-comments">
            <q-item v-if="!comments.length">
              <q-item-section class="text-grey-7">No comments yet.</q-item-section>
            </q-item>
            <q-item v-for="c in comments" :key="c.id" :class="{ 'qa-resolved': c.resolved }">
              <q-item-section>
                <q-item-label class="row items-center gap-xs">
                  <StateChip v-if="c.kind === 'suggestion'" kind="info" label="Suggestion" icon="lightbulb" />
                  <q-chip v-if="c.anchorT !== null || c.anchorLine !== null" dense square clickable size="12px" icon="my_location"
                    color="primary" text-color="white" class="q-ma-none" @click="jump(c)">
                    {{ c.anchorT !== null ? fmtTime(c.anchorT) : `line ${c.anchorLine}` }}
                  </q-chip>
                  <span class="text-caption text-grey-7">{{ c.by }} · {{ fmtAgo(c.at, beacon.now) }}</span>
                </q-item-label>
                <q-item-label class="qa-body q-mt-xs">{{ c.body }}</q-item-label>
                <q-item-label v-if="c.resolved" caption>Resolved by {{ c.resolvedBy }} · {{ fmtAgo(c.resolvedAt, beacon.now) }}</q-item-label>
              </q-item-section>
              <q-item-section side top>
                <div class="row no-wrap">
                  <q-btn flat round dense size="sm" :icon="c.resolved ? 'undo' : 'done'" :disable="busy" @click="resolve(c)">
                    <q-tooltip>{{ c.resolved ? 'Reopen' : 'Mark resolved' }}</q-tooltip>
                  </q-btn>
                  <q-btn v-if="c.mine" flat round dense size="sm" icon="delete_outline" :disable="busy" @click="remove(c)">
                    <q-tooltip>Delete</q-tooltip>
                  </q-btn>
                </div>
              </q-item-section>
            </q-item>
          </q-list>

          <q-separator />
          <q-card-section>
            <q-btn-toggle v-model="kind" dense no-caps unelevated size="sm" toggle-color="primary" color="grey-2" text-color="black" class="q-mb-sm"
              :options="[{ label: 'Comment', value: 'comment', icon: 'chat_bubble_outline' }, { label: 'Suggestion', value: 'suggestion', icon: 'lightbulb' }]" />
            <q-input v-model="draft" outlined autogrow type="textarea" :input-style="{ minHeight: '72px' }"
              :placeholder="kind === 'suggestion' ? 'Suggest a better wording or translation…' : 'What needs attention?'"
              @keydown.ctrl.enter="addComment" @keydown.meta.enter="addComment" />
            <div class="row items-center q-mt-sm">
              <q-checkbox v-if="anchorLabel" v-model="anchor" dense size="sm" :label="`Attach ${anchorLabel}`" />
              <span v-else class="text-caption text-grey-7">General comment (click a line to attach it)</span>
              <q-space />
              <q-btn unelevated no-caps color="primary" label="Add" :disable="!draft.trim()" :loading="busy" @click="addComment" />
            </div>
          </q-card-section>
        </q-card>
      </div>
    </div>
  </q-page>
</template>

<style scoped>
.qa-video { width: 100%; display: block; background: #000; max-height: 70vh; }
.qa-marks { position: relative; height: 12px; margin: 0 12px; }
.qa-mark { position: absolute; top: 2px; width: 8px; height: 8px; margin-left: -4px; border-radius: 50%; background: var(--q-warning); cursor: pointer; }
.qa-mark.mark-suggestion { background: var(--q-info); }
.qa-doc { max-height: 75vh; overflow: auto; }
.qa-line { cursor: pointer; }
.qa-line:hover { background: rgba(0, 0, 0, .04); }
.qa-line.commented { background: rgba(242, 192, 55, .15); }
.qa-line.selected { background: rgba(25, 118, 210, .15); }
.qa-line td.cm { width: 20px; }
.qa-comments { max-height: 50vh; overflow: auto; }
.qa-body { white-space: pre-wrap; }
.qa-resolved { opacity: .55; }
</style>
