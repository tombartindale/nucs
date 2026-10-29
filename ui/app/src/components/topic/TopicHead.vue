<script setup lang="ts">
import { computed, inject } from 'vue';
import type { TopicMark } from '@beacon/shared';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { fmtAgo } from '@/format';
import { useBeacon } from '@/stores/beacon';
import { TOPIC } from './context';

const showScript = defineModel<boolean>('showScript', { required: true });
const showSlides = defineModel<boolean>('showSlides', { required: true });
const showVideo = defineModel<boolean>('showVideo', { required: true });
const t = inject(TOPIC)!;
const beacon = useBeacon();
const st = computed(() => t.status.value);
const title = computed(() => st.value?.title || (t.show.value?.en?.front?.title as string | undefined) || '');
const recordingIn = computed(() => !!st.value?.artifacts.some((a) => a.key === 'edit/master.mp4' && a.exists));
// Same rules as the module grid: each mark implies the one before, a recording implies both.
const MARKS = [
  { key: 'filmed', label: 'Filmed', todo: 'Tick once the recording session has happened' },
  { key: 'edited', label: 'Edited', todo: 'Tick once the edit is done, before it is uploaded' },
] as const;
function markImplied(key: TopicMark): string | null {
  if (recordingIn.value) return 'Recording received';
  if (key === 'filmed' && st.value?.marks.edited) return 'Marked edited, so it has been filmed';
  return null;
}
function markTip(m: typeof MARKS[number]) {
  const by = st.value?.marks[m.key];
  return markImplied(m.key) ?? (by ? `Marked ${m.key} by ${by.by} ${fmtAgo(by.at)}` : m.todo);
}
const blockers = computed(() => (st.value ? [...st.value.en.blockers.map((b) => ({ ...b, lang: 'EN' })), ...st.value.zh.blockers.map((b) => ({ ...b, lang: 'ZH' }))] : []));
</script>

<template>
  <PageHeader :title="`${t.id} · ${title}`"
    :crumbs="[{ label: 'Programme', to: '/' }, { label: t.id.slice(0, 6), to: `/module/${t.id.slice(0, 6)}` }, { label: t.id.slice(7, 10), to: `/module/${t.id.slice(0, 6)}?unit=${t.id.slice(7, 10)}` }, { label: t.id.slice(11) }]">
    <template #sub>
      <div v-if="st" class="row items-center gap-md">
        <StateChip v-if="st.hydration !== 'local' && st.hydration !== 'unknown'" kind="cloud" :label="`☁ ${st.hydration}`" />
        <span v-if="st.minutes" class="text-caption">{{ st.minutes }} min · {{ st.outcomes.join(', ') }}</span>
        <template v-if="st.has_dir">
          <q-checkbox v-for="mk in MARKS" :key="mk.key" dense size="sm" :label="mk.label"
            :model-value="!!st.marks[mk.key] || !!markImplied(mk.key)" :disable="!!markImplied(mk.key)"
            @update:model-value="(v: boolean) => beacon.runJob('mark', [st!.path], v ? { mark: mk.key } : { mark: mk.key, clear: true })">
            <q-tooltip>{{ markTip(mk) }}</q-tooltip>
          </q-checkbox>
        </template>
      </div>
      <q-list v-if="blockers.length" dense class="q-mt-sm">
        <q-item v-for="(b, i) in blockers" :key="i" dense class="q-px-none">
          <q-item-section side><StateChip kind="blocked" :label="`${b.lang} ${b.code}`" /></q-item-section>
          <q-item-section>
            {{ b.message }}
            <span v-if="b.code === 'FS_NOT_HYDRATED'" class="text-caption"> In Finder, choose Always Keep on This Device for this folder.</span>
          </q-item-section>
        </q-item>
      </q-list>
    </template>
    <span class="text-caption text-grey-7">Show</span>
    <q-checkbox v-model="showScript" dense label="Script" />
    <q-checkbox v-model="showSlides" dense label="Slides" />
    <q-checkbox v-model="showVideo" dense label="Video" />
  </PageHeader>
</template>
