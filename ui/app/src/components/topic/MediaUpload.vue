<script setup lang="ts">
// The editor's video and subtitles, uploaded from the browser for this topic. A file that is
// already there is replaced only after confirming: a re-cut moves the cues, so they must be
// regenerated (cues) once the new video is in.
import { computed, inject, onBeforeUnmount, onMounted, reactive } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { confirm } from '@/composables/confirm';
import { type Abortable, type UploadKind, UPLOAD_KINDS, uploadTopicFile } from '@/composables/topicUpload';
import { useBeacon } from '@/stores/beacon';
import { TOPIC } from './context';

type Kind = UploadKind;
const KINDS = UPLOAD_KINDS;

const t = inject(TOPIC)!;
const beacon = useBeacon();
const busy = reactive<Partial<Record<Kind, boolean>>>({});
// Bytes sent so far, as a fraction; absent once the upload has finished and the server is
// placing the file and running cues/validate, which fetch() progress events cannot see.
const sent = reactive<Partial<Record<Kind, number>>>({});
// Tracked so a confirmed navigate-away (see onBeforeRouteLeave below) can abort the
// transfer outright, rather than leaving it running against a component that's gone.
const inFlight: Partial<Record<Kind, Abortable>> = {};
const anyBusy = computed(() => Object.values(busy).some(Boolean));

// Full browser navigation (close tab, refresh, type a new URL) can't be cancelled from
// here; the browser kills the transfer outright, so the best this can do is ask first,
// via the native prompt.
function onUnload(e: BeforeUnloadEvent) { if (anyBusy.value) { e.preventDefault(); e.returnValue = ''; } }
onMounted(() => window.addEventListener('beforeunload', onUnload));
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onUnload);
  for (const xhr of Object.values(inFlight)) xhr.abort();
});

// In-app navigation (another topic, the module page) doesn't unload the document, so it
// would otherwise leave the transfer running against a component that's about to be torn
// down — ask first, and only abort (in onBeforeUnmount above) once the user confirms.
onBeforeRouteLeave(async () => {
  if (!anyBusy.value) return true;
  const leave = await confirm({
    title: 'An upload is still in progress',
    lines: ['Leaving now cancels it — the file will need to be uploaded again.'],
    ok: 'Leave and cancel upload',
    danger: true,
  });
  return leave === true;
});

const present = computed<Record<Kind, boolean>>(() => {
  const m = t.show.value?.media;
  const zhMd = !!t.status.value?.artifacts.find((a) => a.key === 'topic.zh.md')?.exists;
  return { master: !!m?.master, en: !!m?.subtitles.en, zh: !!m?.subtitles.zh, zh_md: zhMd };
});

async function upload(kind: Kind, file: File | null) {
  if (!file) return;
  const spec = KINDS.find((k) => k.kind === kind)!;
  const replacing = present.value[kind];
  if (replacing && !(await confirm({
    title: `Replace ${spec.label.toLowerCase()} for ${t.id}?`,
    lines: [
      `This replaces ${spec.path} with ${file.name}.`,
      'If the video changed, its cues must be regenerated: run cues again once it is in.',
    ],
    ok: 'Replace',
    danger: true,
  }))) return;

  busy[kind] = true;
  sent[kind] = 0;
  try {
    const job = await uploadTopicFile(t.id, kind, file, replacing, (a) => { inFlight[kind] = a; },
      (fraction) => { sent[kind] = fraction; });
    delete sent[kind];
    beacon.trackJob(job);
    const result = await beacon.awaitJob(job.id);
    await t.reload();
    if (result.state === 'done') beacon.toast(`${spec.label} placed for ${t.id}.`);
    else beacon.toast(`${spec.label} was not placed; see Jobs.`, true);
  } catch (e) {
    beacon.toast((e as Error).message, true);
  } finally {
    busy[kind] = false;
    delete sent[kind];
    delete inFlight[kind];
  }
}
</script>

<template>
  <q-card-section class="q-pt-none">
    <div class="row q-col-gutter-sm">
      <div v-for="k in KINDS" :key="k.kind" class="col-12 col-sm-6 col-md-3">
        <q-file :model-value="null" dense outlined clearable :accept="k.accept" :disable="!!busy[k.kind]"
          :label="present[k.kind] ? `Replace ${k.label.toLowerCase()}` : `Upload ${k.label.toLowerCase()}`"
          :hint="present[k.kind] ? `Present: ${k.path}` : `Goes to ${k.path}`"
          :aria-label="`Upload ${k.label}`" @update:model-value="(f: File | null) => upload(k.kind, f)">
          <template #prepend><q-icon :name="k.icon" /></template>
        </q-file>
        <div v-if="busy[k.kind]" class="q-mt-xs">
          <template v-if="sent[k.kind] !== undefined">
            <q-linear-progress :value="sent[k.kind]" size="6px" rounded color="primary" />
            <div class="text-caption text-grey-7">Uploading… {{ Math.round((sent[k.kind] ?? 0) * 100) }}%</div>
          </template>
          <div v-else class="row items-center gap-sm text-caption text-grey-7">
            <q-spinner color="primary" size="16px" />
            <span>Placing on the server…</span>
          </div>
        </div>
      </div>
    </div>
  </q-card-section>
</template>
