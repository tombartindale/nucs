<script setup lang="ts">
// Mandarin QA task list: every composed Mandarin video and Mandarin activity that exists,
// with where its internal review stands. Click one to watch or read it and comment.
import { computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import type { QaItem } from '@beacon/shared';
import PageHeader from '@/components/PageHeader.vue';
import StateChip from '@/components/StateChip.vue';
import { outstanding, useQaFilter, type QaShow } from '@/composables/qaFilter';
import { fmtAgo, plural, QA_STATUS } from '@/format';
import { useBeacon } from '@/stores/beacon';

const beacon = useBeacon();
const router = useRouter();
const { filter, items } = useQaFilter();
onMounted(() => { void beacon.loadQa().catch((e) => beacon.toast((e as Error).message, true)); });

const all = computed(() => beacon.qa || []);
const count = (fn: (i: QaItem) => boolean) => all.value.filter((i) => (!filter.module || i.module === filter.module)
  && (!filter.type || i.type === filter.type) && fn(i)).length;
const SHOWS = computed<Array<{ value: QaShow; label: string; n: number }>>(() => [
  { value: 'outstanding', label: 'To review', n: count(outstanding) },
  { value: 'changes_needed', label: 'Changes needed', n: count((i) => i.status === 'changes_needed') },
  { value: 'approved', label: 'Approved', n: count((i) => i.status === 'approved') },
  { value: 'all', label: 'All', n: count(() => true) },
]);
const modules = computed(() => [...new Set(all.value.map((i) => i.module))].sort());
const TYPES = [{ label: 'All types', value: null }, { label: 'Videos', value: 'video' }, { label: 'Activities', value: 'activity' }];

const columns = [
  { name: 'type', label: '', field: 'type', align: 'left' as const, style: 'width: 40px' },
  { name: 'what', label: 'Item', field: 'item', align: 'left' as const },
  { name: 'status', label: 'QA status', field: 'status', align: 'left' as const },
  { name: 'comments', label: 'Comments', field: 'openComments', align: 'left' as const },
  { name: 'activity', label: 'Last activity', field: 'lastActivity', align: 'left' as const },
];
const code = (i: QaItem) => (i.topic ?? `${i.module}-${i.unit}`);
const open = (i: QaItem) => router.push(`/qa/${i.item}`);
const sub = computed(() => {
  const n = all.value.filter(outstanding).length;
  return n ? `${plural(n, 'item')} waiting for review.` : 'Nothing waiting for review.';
});
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Mandarin QA" :sub="sub" :crumbs="[{ label: 'Programme', to: '/' }, { label: 'QA' }]" />

    <div class="row items-center gap-sm q-mb-md">
      <q-btn-toggle v-model="filter.show" no-caps unelevated toggle-color="primary" color="grey-2" text-color="black"
        :options="SHOWS.map((s) => ({ value: s.value, slot: s.value }))">
        <template v-for="s in SHOWS" :key="s.value" #[s.value]>
          {{ s.label }}<q-badge rounded class="q-ml-xs" :color="filter.show === s.value ? 'white' : 'grey-5'"
            :text-color="filter.show === s.value ? 'primary' : 'black'">{{ s.n }}</q-badge>
        </template>
      </q-btn-toggle>
      <q-space />
      <q-select v-model="filter.module" dense outlined emit-value map-options clearable style="min-width: 150px"
        :options="modules.map((m) => ({ label: m, value: m }))" label="Module" />
      <q-select v-model="filter.type" dense outlined emit-value map-options style="min-width: 140px" :options="TYPES" />
    </div>

    <q-banner v-if="beacon.qa === null" rounded class="bg-grey-2">Loading…</q-banner>
    <q-card v-else flat bordered>
      <q-table flat :rows="items" :columns="columns" row-key="item" :pagination="{ rowsPerPage: 50 }"
        :rows-per-page-options="[25, 50, 100, 0]" class="qa-table" @row-click="(_e: Event, row: QaItem) => open(row)">
        <template #body-cell-type="p">
          <q-td :props="p">
            <q-icon :name="p.row.type === 'video' ? 'movie' : 'assignment'" size="20px" color="grey-7">
              <q-tooltip>{{ p.row.type === 'video' ? 'Composed Mandarin video' : 'Mandarin activity' }}</q-tooltip>
            </q-icon>
          </q-td>
        </template>
        <template #body-cell-what="p">
          <q-td :props="p">
            <div class="text-weight-medium">{{ p.row.title }}</div>
            <div class="text-caption text-grey-7">
              {{ code(p.row) }} · {{ p.row.type === 'video' ? 'Video' : 'Activity' }}
              <span v-if="p.row.stale" class="text-warning"> · out of date</span>
            </div>
          </q-td>
        </template>
        <template #body-cell-status="p">
          <q-td :props="p">
            <div class="row items-center gap-xs no-wrap">
              <StateChip :kind="QA_STATUS[p.row.status].kind" :label="QA_STATUS[p.row.status].label" :icon="QA_STATUS[p.row.status].icon" />
              <StateChip v-if="p.row.changedSinceReview" kind="warn" label="Changed since" icon="update">
                <q-tooltip>The file has changed since it was marked {{ QA_STATUS[p.row.status].label.toLowerCase() }}: look again.</q-tooltip>
              </StateChip>
            </div>
            <div v-if="p.row.statusBy" class="text-caption text-grey-7">{{ p.row.statusBy }}</div>
          </q-td>
        </template>
        <template #body-cell-comments="p">
          <q-td :props="p">
            <span v-if="!p.row.comments" class="text-grey-6">—</span>
            <span v-else>
              <q-icon name="chat_bubble_outline" size="16px" class="q-mr-xs" />
              <b v-if="p.row.openComments">{{ p.row.openComments }} open</b>
              <span v-else class="text-grey-7">all resolved</span>
              <span class="text-grey-7"> / {{ p.row.comments }}</span>
            </span>
          </q-td>
        </template>
        <template #body-cell-activity="p">
          <q-td :props="p" class="text-grey-8">{{ fmtAgo(p.row.lastActivity, beacon.now) }}</q-td>
        </template>
        <template #no-data>
          <div class="full-width text-center text-grey-7 q-pa-md">
            {{ all.length ? 'Nothing here with these filters.' : 'Nothing is ready for QA yet: items appear once a topic\'s Mandarin video is composed, or a unit\'s Mandarin activity exists.' }}
          </div>
        </template>
      </q-table>
    </q-card>
  </q-page>
</template>

<style scoped>
.qa-table :deep(tbody tr) { cursor: pointer; }
</style>
