<script setup lang="ts">
// Which theme is actually resolved on this server, and whether it is complete: the same
// check bcn bumpers/render would hit (bcn themecheck).
import { onMounted, ref, shallowRef } from 'vue';
import type { Diagnostic } from '@beacon/shared';
import { api } from '@/api';
import { useBeacon } from '@/stores/beacon';

interface ThemeCheck {
  theme: string; resolved: boolean; dir: string | null; custom: boolean; css: string | null; files: string[];
  bumper_logo: string | null; bumper_background_video: string | null; document_logo: string | null; font_faces: string[];
  toml_text: string | null;
  diagnostics: Diagnostic[];
}

const beacon = useBeacon();
const theme = shallowRef<ThemeCheck | null>(null);
const loading = ref(false);

// Which role a file plays, so the list isn't just an undifferentiated pile -- font files
// especially tend to be many and otherwise indistinguishable.
function roleOf(t: ThemeCheck, file: string): string | null {
  if (file === t.css) return 'stylesheet';
  if (file === t.bumper_logo) return 'bumper logo';
  if (file === t.bumper_background_video) return 'bumper background video';
  if (file === t.document_logo) return 'printed document logo';
  if (t.font_faces.includes(file)) return 'font';
  return null;
}

async function refresh() {
  loading.value = true;
  try { theme.value = await api<ThemeCheck>('/api/admin/theme'); } catch (e) { beacon.toast((e as Error).message, true); }
  loading.value = false;
}

function downloadToml(t: ThemeCheck) {
  if (!t.toml_text) return;
  const url = URL.createObjectURL(new Blob([t.toml_text], { type: 'text/plain' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `${t.theme}.theme.toml`;
  a.click();
  URL.revokeObjectURL(url);
}

onMounted(refresh);
</script>

<template>
  <q-card flat bordered>
    <q-card-section class="row items-center">
      <div class="text-subtitle1 text-weight-medium">Active theme</div>
      <q-space />
      <q-btn flat dense no-caps icon="refresh" label="Refresh" :loading="loading" @click="refresh" />
    </q-card-section>
    <template v-if="theme">
      <q-banner v-if="!theme.resolved" class="bg-negative text-white" rounded>
        <div class="text-weight-medium">The "{{ theme.theme }}" theme does not resolve.</div>
        <div v-for="(d, i) in theme.diagnostics" :key="i">{{ d.message }}</div>
        <div class="text-caption q-mt-xs">bcn bumpers/render will fail the same way until this is fixed.</div>
      </q-banner>
      <template v-else>
        <q-card-section class="row items-center gap-sm q-pb-sm">
          <span>Active theme: <strong>{{ theme.theme }}</strong></span>
          <q-chip dense square :color="theme.custom ? 'primary' : 'grey-6'" text-color="white" :label="theme.custom ? 'uploaded custom theme' : 'bundled with the image'" />
          <q-banner v-if="theme.diagnostics.length" class="bg-orange-2 text-black q-mt-sm" rounded dense>
            <div v-for="(d, i) in theme.diagnostics" :key="i">{{ d.message }}</div>
          </q-banner>
        </q-card-section>
        <q-card-section class="text-caption text-grey-7 q-pt-none">{{ theme.dir }}</q-card-section>
        <q-list separator dense>
          <q-item v-for="f in theme.files" :key="f">
            <q-item-section class="text-mono">{{ f }}</q-item-section>
            <q-item-section v-if="roleOf(theme, f)" side class="text-caption text-grey-7">{{ roleOf(theme, f) }}</q-item-section>
          </q-item>
        </q-list>
      </template>
      <q-card-section v-if="theme.toml_text" class="q-pt-none">
        <q-expansion-item dense icon="description" :label="`theme.toml (${theme.theme})`">
          <template #header>
            <q-item-section>theme.toml ({{ theme.theme }})</q-item-section>
            <q-item-section side>
              <q-btn flat dense no-caps icon="download" label="Download" @click.stop="downloadToml(theme)" />
            </q-item-section>
          </template>
          <pre class="text-mono text-caption text-black q-pa-sm bg-grey-2" style="white-space: pre-wrap; overflow-x: auto;">{{ theme.toml_text }}</pre>
        </q-expansion-item>
      </q-card-section>
    </template>
    <q-card-section v-else-if="!loading" class="text-grey-7">Could not load theme status.</q-card-section>
  </q-card>
</template>
