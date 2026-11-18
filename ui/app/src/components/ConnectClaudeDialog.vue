<script setup lang="ts">
// Connect Claude Desktop: download the NUCS extension, create a personal API token for it,
// and see or revoke the tokens already made. The token is shown once, here, and never again.
import { computed, ref, shallowRef, watch } from 'vue';
import type { ApiTokenCreated, ApiTokenInfo, ApiTokensResponse, McpInfoResponse } from '@beacon/shared';
import { api } from '@/api';
import { confirm } from '@/composables/confirm';
import { fmtAgo } from '@/format';
import { useBeacon } from '@/stores/beacon';

const open = defineModel<boolean>({ default: false });
const beacon = useBeacon();
const info = shallowRef<McpInfoResponse | null>(null);
const tokens = shallowRef<ApiTokenInfo[]>([]);
const created = shallowRef<ApiTokenCreated | null>(null);
const name = ref('');
const busy = ref(false);

// The address this browser reached NUCS at is the one Claude Desktop should use too.
const serverUrl = computed(() => window.location.origin);

async function load() {
  try {
    [info.value, tokens.value] = await Promise.all([
      api<McpInfoResponse>('/api/mcp/info'),
      api<ApiTokensResponse>('/api/tokens').then((r) => r.tokens),
    ]);
  } catch (e) { beacon.toast((e as Error).message, true); }
}

watch(open, (v) => {
  if (v) {
    created.value = null;
    name.value = `Claude Desktop – ${new Date().toLocaleDateString()}`;
    void load();
  }
});

async function create() {
  busy.value = true;
  try {
    created.value = await api<ApiTokenCreated>('/api/tokens', { method: 'POST', body: { name: name.value } });
    await load();
  } catch (e) { beacon.toast((e as Error).message, true); }
  busy.value = false;
}

async function revoke(t: ApiTokenInfo) {
  const ok = await confirm({ title: `Revoke "${t.name}"?`, danger: true, ok: 'Revoke',
    lines: ['Claude Desktop using this token loses access to NUCS at once. Create a new token to reconnect it.'] });
  if (!ok) return;
  try {
    await api(`/api/tokens/${t.id}`, { method: 'DELETE' });
    if (created.value?.id === t.id) created.value = null;
    await load();
  } catch (e) { beacon.toast((e as Error).message, true); }
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); beacon.toast('Copied'); } catch { beacon.toast('Could not copy', true); }
}

const fallback = computed(() => JSON.stringify({
  mcpServers: {
    nucs: {
      command: 'npx',
      args: ['-y', 'mcp-remote', `${serverUrl.value}/mcp`, '--header', `Authorization: Bearer ${created.value?.token ?? 'nucs_…'}`],
    },
  },
}, null, 2));
</script>

<template>
  <q-dialog v-model="open">
    <q-card style="width: 640px; max-width: 95vw">
      <q-card-section class="row items-center">
        <q-icon name="smart_toy" size="sm" class="q-mr-sm" />
        <div class="text-h6">Connect Claude Desktop</div>
        <q-space />
        <q-btn flat round dense icon="close" v-close-popup aria-label="Close" />
      </q-card-section>

      <q-card-section class="q-pt-none text-body2">
        Lets Claude in the Claude Desktop app read NUCS (status, scripts, subtitles, documents, diagnostics), add QA comments and
        verdicts, and check and import module content from folders on your computer. Everything it writes is recorded as you,
        marked "via Claude".
      </q-card-section>

      <q-list>
        <q-item>
          <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">1</q-avatar></q-item-section>
          <q-item-section>
            <q-item-label>Download the NUCS extension</q-item-label>
            <q-item-label caption>A one-click Claude Desktop extension (.mcpb). You only need it once.</q-item-label>
          </q-item-section>
          <q-item-section side>
            <q-btn v-if="info?.extension" unelevated dense no-caps color="primary" icon="download" label="nucs.mcpb"
              href="/api/mcp/extension" download="nucs.mcpb" />
            <span v-else-if="info" class="text-caption text-grey-7">Not built on this server</span>
          </q-item-section>
        </q-item>

        <q-item>
          <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">2</q-avatar></q-item-section>
          <q-item-section>
            <q-item-label>Create a token</q-item-label>
            <q-input v-if="!created" v-model="name" dense outlined label="Name (which computer it's for)" class="q-mt-sm" maxlength="80" />
          </q-item-section>
          <q-item-section v-if="!created" side>
            <q-btn unelevated dense no-caps color="primary" label="Create token" :loading="busy" @click="create" />
          </q-item-section>
        </q-item>

        <q-item v-if="created">
          <q-item-section avatar />
          <q-item-section>
            <q-banner dense rounded class="bg-orange-1 text-black q-mb-sm">
              <template #avatar><q-icon name="warning" color="orange-8" /></template>
              Copy this token now. It won't be shown again; if you lose it, revoke it and create another.
            </q-banner>
            <div class="row items-center no-wrap q-gutter-x-sm">
              <code class="col bg-grey-2 text-black q-pa-sm rounded-borders" style="overflow-wrap: anywhere">{{ created.token }}</code>
              <q-btn flat dense no-caps icon="content_copy" label="Copy" @click="copy(created.token)" />
            </div>
          </q-item-section>
        </q-item>

        <q-item>
          <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">3</q-avatar></q-item-section>
          <q-item-section>
            <q-item-label>Install it</q-item-label>
            <q-item-label caption class="q-mt-xs">
              Double-click <b>nucs.mcpb</b> (or in Claude Desktop: Settings → Extensions → Install extension) and fill in:
            </q-item-label>
            <div class="row items-center no-wrap q-gutter-x-sm q-mt-xs">
              <span class="text-caption" style="min-width: 110px">NUCS address</span>
              <code class="col bg-grey-2 text-black q-px-sm rounded-borders">{{ serverUrl }}</code>
              <q-btn flat dense round size="sm" icon="content_copy" aria-label="Copy address" @click="copy(serverUrl)" />
            </div>
            <div class="row items-center no-wrap q-gutter-x-sm q-mt-xs">
              <span class="text-caption" style="min-width: 110px">API token</span>
              <span class="col text-caption text-grey-8">the token from step 2</span>
            </div>
            <div class="row items-center no-wrap q-gutter-x-sm q-mt-xs">
              <span class="text-caption" style="min-width: 110px">Import folders</span>
              <span class="col text-caption text-grey-8">optional; your Downloads folder if left empty</span>
            </div>
          </q-item-section>
        </q-item>

        <q-item>
          <q-item-section avatar><q-avatar size="28px" color="primary" text-color="white">4</q-avatar></q-item-section>
          <q-item-section>
            <q-item-label>Check it works</q-item-label>
            <q-item-label caption>In a new Claude chat, ask: <i>"What modules are in NUCS?"</i></q-item-label>
          </q-item-section>
        </q-item>
      </q-list>

      <q-expansion-item dense dense-toggle icon="build" label="If extensions are turned off on your Claude account"
        header-class="text-caption text-grey-8">
        <q-card-section class="q-pt-none text-caption">
          Add this to Claude Desktop's configuration (Settings → Developer → Edit Config). It needs Node.js installed, and
          can't import files from your computer; use the Intake page for that.
          <pre class="bg-grey-2 text-black q-pa-sm rounded-borders q-mt-sm" style="white-space: pre-wrap; overflow-wrap: anywhere">{{ fallback }}</pre>
          <q-btn flat dense no-caps icon="content_copy" label="Copy" @click="copy(fallback)" />
        </q-card-section>
      </q-expansion-item>

      <q-separator />
      <q-card-section class="text-subtitle2">Your tokens</q-card-section>
      <q-markup-table v-if="tokens.length" flat dense>
        <thead><tr><th class="text-left">Name</th><th class="text-left">Created</th><th class="text-left">Last used</th><th></th></tr></thead>
        <tbody>
          <tr v-for="t in tokens" :key="t.id">
            <td>{{ t.name }}</td>
            <td class="text-caption">{{ fmtAgo(t.created, beacon.now) }}</td>
            <td class="text-caption">{{ t.lastUsed ? fmtAgo(t.lastUsed, beacon.now) : 'never' }}</td>
            <td class="text-right"><q-btn flat dense size="sm" no-caps color="negative" label="Revoke" @click="revoke(t)" /></td>
          </tr>
        </tbody>
      </q-markup-table>
      <q-card-section v-else class="q-pt-none text-grey-7 text-caption">None yet.</q-card-section>
    </q-card>
  </q-dialog>
</template>
