<script setup lang="ts">
// Remote runners: machines outside this server that take the heavy jobs routed to them
// (see deploy/RUNNER.md), and where each command that can run on one is sent.
import { computed, onMounted, onUnmounted, reactive, ref, shallowRef } from 'vue';
import type { Prefs, Routing, RunnersResponse, RunnerTokenResponse } from '@beacon/shared';
import { api } from '@/api';
import { confirm } from '@/composables/confirm';
import { fmtAgo } from '@/format';
import StateChip from '@/components/StateChip.vue';
import { useBeacon } from '@/stores/beacon';

const beacon = useBeacon();
const data = shallowRef<RunnersResponse | null>(null);
const token = shallowRef<RunnerTokenResponse | null>(null);
const routing = reactive<Record<string, Routing>>({ ...(beacon.boot!.prefs.routing || {}) });
const labels = reactive<Record<string, string>>(Object.fromEntries(
  Object.entries(beacon.boot!.prefs.routing_labels || {}).map(([k, v]) => [k, v.join(', ')])));
const saving = ref(false);
let timer: ReturnType<typeof setInterval> | undefined;

const MODES: { label: string; value: Routing }[] = [
  { label: 'This server', value: 'local' },
  { label: 'A runner when one is online', value: 'auto' },
  { label: 'Runners only (wait for one)', value: 'remote' },
];

async function refresh() {
  try { data.value = await api<RunnersResponse>('/api/admin/runners'); } catch (e) { beacon.toast((e as Error).message, true); }
}

async function addRunner() {
  try { token.value = await api<RunnerTokenResponse>('/api/admin/runners/tokens', { method: 'POST', body: {} }); }
  catch (e) { beacon.toast((e as Error).message, true); }
}

const command = computed(() => !token.value ? '' : [
  'docker run -d --name beacon-runner --restart unless-stopped \\',
  `  -e NUCS_URL=${window.location.origin} \\`,
  `  -e RUNNER_REGISTRATION_TOKEN=${token.value.token} \\`,
  '  -v beacon-runner:/data/scratch \\',
  '  --entrypoint node ghcr.io/<owner>/nucs:latest \\',
  '  /app/ui/server/dist/runner.js --slots 2 --commands compose',
].join('\n'));

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); beacon.toast('Copied'); } catch { beacon.toast('Could not copy', true); }
}

async function remove(id: string, name: string) {
  const ok = await confirm({ title: `Remove ${name}?`, danger: true, ok: 'Remove',
    lines: ['Its token stops working at once. A job it is running is handed back when its lease runs out.'] });
  if (!ok) return;
  try { await api(`/api/admin/runners/${id}`, { method: 'DELETE' }); await refresh(); } catch (e) { beacon.toast((e as Error).message, true); }
}

async function saveRouting() {
  saving.value = true;
  try {
    const routing_labels = Object.fromEntries(Object.entries(labels)
      .map(([k, v]) => [k, v.split(',').map((x) => x.trim()).filter(Boolean)]));
    beacon.boot!.prefs = await api<Prefs>('/api/prefs', { method: 'PUT', body: { routing: { ...routing }, routing_labels } });
    beacon.toast('Saved');
  } catch (e) { beacon.toast((e as Error).message, true); }
  saving.value = false;
}

const stale = (v: string) => !!data.value && data.value.server_version !== 'dev' && v !== data.value.server_version;

onMounted(() => { void refresh(); timer = setInterval(() => void refresh(), 10_000); });
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <q-card flat bordered>
    <q-card-section class="row items-center">
      <div class="text-subtitle1 text-weight-medium">Runners</div>
      <q-space />
      <q-btn flat dense no-caps icon="add" label="Add a runner" @click="addRunner" />
    </q-card-section>
    <q-card-section class="text-caption text-grey-7 q-pt-none">
      Other machines that take heavy jobs (compose) off this server. A runner only connects out to this server over HTTPS,
      and reaches video with temporary credentials this server issues for each job. See deploy/RUNNER.md.
    </q-card-section>
    <q-card-section v-if="token" class="q-pt-none">
      <div class="text-body2 q-mb-sm">
        Start the runner on the other machine with this one-time token (valid until {{ new Date(token.expires).toLocaleString() }}):
      </div>
      <pre class="text-mono text-black q-pa-sm bg-grey-2 rounded-borders" style="white-space: pre-wrap; overflow-wrap: anywhere">{{ command }}</pre>
      <q-btn flat dense no-caps icon="content_copy" label="Copy" @click="copy(command)" />
    </q-card-section>
    <q-markup-table v-if="data?.runners.length" flat dense>
      <thead>
        <tr><th class="text-left">Name</th><th></th><th class="text-left">Takes</th><th class="text-right">Slots</th>
          <th class="text-right">Running</th><th class="text-left">Version</th><th class="text-left">Last seen</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="r in data.runners" :key="r.id">
          <td class="text-weight-medium">{{ r.name }}</td>
          <td><StateChip :kind="r.online ? 'ok' : undefined" :label="r.online ? 'online' : 'offline'" /></td>
          <td class="text-caption">
            {{ r.commands.length ? r.commands.join(', ') : 'any' }}<template v-if="r.labels.length"> · {{ r.labels.join(', ') }}</template>
          </td>
          <td class="text-right">{{ r.slots }}</td>
          <td class="text-right">{{ r.running }}</td>
          <td class="text-caption text-mono" :class="{ 'text-negative': stale(r.version) }">
            {{ (r.version || '—').slice(0, 12) }}<template v-if="stale(r.version)"> (update it)</template>
          </td>
          <td class="text-caption">{{ r.last_seen ? fmtAgo(r.last_seen, beacon.now) : 'never' }}</td>
          <td class="text-right"><q-btn flat dense size="sm" no-caps color="negative" label="Remove" @click="remove(r.id, r.name)" /></td>
        </tr>
      </tbody>
    </q-markup-table>
    <q-card-section v-else-if="data" class="text-grey-7">No runners yet.</q-card-section>

    <q-separator />
    <q-card-section class="text-subtitle2">Where jobs run</q-card-section>
    <q-card-section class="q-pt-none q-gutter-y-sm">
      <div v-for="cmd in data?.remote_commands || []" :key="cmd" class="row items-center q-col-gutter-sm">
        <div class="col-12 col-sm-2 text-weight-medium">{{ cmd }}</div>
        <q-select class="col-12 col-sm-5" outlined dense emit-value map-options :options="MODES"
          :model-value="routing[cmd] || 'local'" @update:model-value="(v: Routing) => (routing[cmd] = v)" />
        <q-input v-model="labels[cmd]" class="col-12 col-sm-5" outlined dense label="Runner labels required"
          hint="Comma-separated, e.g. heavy" :disable="(routing[cmd] || 'local') === 'local'" />
      </div>
    </q-card-section>
    <q-card-actions><q-btn unelevated color="primary" label="Save" :loading="saving" @click="saveRouting" /></q-card-actions>
  </q-card>
</template>
