<script setup lang="ts">
// Backups kept in S3 (nightly, deploy/backup/backup.sh). Full programme backups can be
// downloaded or restored here; a restore merges into the live programme, reporting anything
// that differs rather than overwriting it. Database dumps are download-only: restoring one
// replaces the whole database, so that stays a deliberate command-line step.
import { computed, onMounted, ref, shallowRef } from 'vue';
import type { JobSummary } from '@beacon/shared';
import { api } from '@/api';
import PageHeader from '@/components/PageHeader.vue';
import { confirm } from '@/composables/confirm';
import { fmtBytes } from '@/format';
import { useBeacon } from '@/stores/beacon';

interface BackupItem { key: string; name: string; kind: 'full' | 'db'; bytes: number; modified: string }
interface BackupList { enabled: boolean; items: BackupItem[] }

const beacon = useBeacon();
const list = shallowRef<BackupList>({ enabled: false, items: [] });
const loading = ref(false);
const restoring = ref<string | null>(null);

const full = computed(() => list.value.items.filter((i) => i.kind === 'full'));
const db = computed(() => list.value.items.filter((i) => i.kind === 'db'));

async function refresh() {
  loading.value = true;
  try { list.value = await api<BackupList>('/api/admin/backups'); } catch (e) { beacon.toast((e as Error).message, true); }
  loading.value = false;
}

function download(item: BackupItem) {
  window.location.href = `/api/admin/backups/download?key=${encodeURIComponent(item.key)}`;
}

async function restore(item: BackupItem) {
  const ok = await confirm({
    title: `Restore ${item.name}?`,
    lines: [
      'Programme files from this backup are merged into the live programme.',
      'Anything that already exists with different content is reported and left as it is, not overwritten.',
      'Modules and pipeline state that are not in the backup are left alone.',
    ],
    ok: 'Restore',
  });
  if (ok !== true) return;
  restoring.value = item.key;
  try {
    const job = await api<JobSummary>('/api/admin/backups/restore', { body: { key: item.key } });
    beacon.trackJob(job);
    const done = await beacon.awaitJob(job.id);
    if (done.state === 'done') beacon.toast('Backup restored. See the Verification page for anything left to check.');
    else beacon.toast('The restore did not finish cleanly; see Jobs.', true);
  } catch (e) { beacon.toast((e as Error).message, true); }
  restoring.value = null;
}

onMounted(refresh);
</script>

<template>
  <q-page padding class="page-max">
    <PageHeader title="Backups" sub="Nightly copies kept in S3. A full programme backup can be downloaded or restored; a database dump can be downloaded." />
    <q-banner v-if="!list.enabled && !loading" class="bg-orange-2 text-black q-mb-md" rounded>
      Backups are not configured on this server (S3_BACKUP_BUCKET is not set).
    </q-banner>

    <q-card flat bordered class="q-mb-md">
      <q-card-section class="row items-center">
        <div class="text-subtitle1 text-weight-medium">Full programme backups</div>
        <q-space />
        <q-btn flat dense no-caps icon="refresh" label="Refresh" :loading="loading" @click="refresh" />
      </q-card-section>
      <q-list v-if="full.length" separator>
        <q-item v-for="b in full" :key="b.key">
          <q-item-section>
            <q-item-label class="text-mono">{{ b.name }}</q-item-label>
            <q-item-label caption>{{ new Date(b.modified).toLocaleString() }} · {{ fmtBytes(b.bytes) }}</q-item-label>
          </q-item-section>
          <q-item-section side>
            <div class="row no-wrap q-gutter-xs">
              <q-btn outline dense size="sm" no-caps icon="download" label="Download" @click="download(b)" />
              <q-btn outline dense size="sm" no-caps icon="settings_backup_restore" label="Restore" color="warning"
                :loading="restoring === b.key" :disable="!!restoring" @click="restore(b)" />
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <q-card-section v-else class="text-grey-7">No full programme backups yet.</q-card-section>
    </q-card>

    <q-card flat bordered>
      <q-card-section class="text-subtitle1 text-weight-medium">Database dumps</q-card-section>
      <q-list v-if="db.length" separator>
        <q-item v-for="b in db" :key="b.key">
          <q-item-section>
            <q-item-label class="text-mono">{{ b.name }}</q-item-label>
            <q-item-label caption>{{ new Date(b.modified).toLocaleString() }} · {{ fmtBytes(b.bytes) }}</q-item-label>
          </q-item-section>
          <q-item-section side>
            <q-btn outline dense size="sm" no-caps icon="download" label="Download" @click="download(b)" />
          </q-item-section>
        </q-item>
      </q-list>
      <q-card-section v-else class="text-grey-7">No database dumps yet.</q-card-section>
      <q-card-section class="text-caption text-grey-7">
        Restoring a database dump replaces the whole database (job history, preferences, delivery plans), so it is done from the command line: <span class="text-mono">restore.sh</span>.
      </q-card-section>
    </q-card>
  </q-page>
</template>
