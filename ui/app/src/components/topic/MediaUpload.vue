<script setup lang="ts">
// The editor's video and subtitles, uploaded from the browser for this topic. A file that is
// already there is replaced only after confirming: a re-cut moves the cues, so they must be
// regenerated (cues) once the new video is in.
import { computed, inject, reactive } from 'vue';
import type { JobSummary } from '@beacon/shared';
import { api } from '@/api';
import { confirm } from '@/composables/confirm';
import { useBeacon } from '@/stores/beacon';
import { TOPIC } from './context';

type Kind = 'master' | 'en' | 'zh';
const KINDS: Array<{ kind: Kind; label: string; accept: string; path: string }> = [
  { kind: 'master', label: 'Video', accept: '.mp4,video/mp4', path: 'edit/master.mp4' },
  { kind: 'en', label: 'English subtitles', accept: '.srt', path: 'edit/master.srt' },
  { kind: 'zh', label: 'Mandarin subtitles', accept: '.srt', path: 'edit/master.zh.srt' },
];

const t = inject(TOPIC)!;
const beacon = useBeacon();
const busy = reactive<Partial<Record<Kind, boolean>>>({});

const present = computed<Record<Kind, boolean>>(() => {
  const m = t.show.value?.media;
  return { master: !!m?.master, en: !!m?.subtitles.en, zh: !!m?.subtitles.zh };
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
  try {
    const query = `kind=${kind}${replacing ? '&replace=1' : ''}`;
    const job = await api<JobSummary>(`/api/topic/${t.id}/media?${query}`, { raw: file });
    beacon.trackJob(job);
    const result = await beacon.awaitJob(job.id);
    await t.reload();
    if (result.state === 'done') beacon.toast(`${spec.label} placed for ${t.id}.`);
    else beacon.toast(`${spec.label} was not placed; see Jobs.`, true);
  } catch (e) {
    beacon.toast((e as Error).message, true);
  } finally {
    busy[kind] = false;
  }
}
</script>

<template>
  <q-card-section class="q-pt-none">
    <div class="row q-col-gutter-sm">
      <div v-for="k in KINDS" :key="k.kind" class="col-12 col-sm-4">
        <q-file :model-value="null" dense outlined clearable :accept="k.accept" :disable="!!busy[k.kind]" :loading="!!busy[k.kind]"
          :label="present[k.kind] ? `Replace ${k.label.toLowerCase()}` : `Upload ${k.label.toLowerCase()}`"
          :hint="present[k.kind] ? `Present: ${k.path}` : `Goes to ${k.path}`"
          :aria-label="`Upload ${k.label}`" @update:model-value="(f: File | null) => upload(k.kind, f)">
          <template #prepend><q-icon :name="k.kind === 'master' ? 'videocam' : 'subtitles'" /></template>
        </q-file>
      </div>
    </div>
  </q-card-section>
</template>
